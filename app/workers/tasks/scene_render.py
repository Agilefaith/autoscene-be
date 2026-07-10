"""Scene motion-render stage (AutoScene §4.3 / §4.4).

Downloads each scene's SDXL image(s) and renders a moving clip via the FFmpeg motion
engine: Ken Burns for Mode 1, per-image motion + crossfade for Mode 2. Clips are
rendered at the synced per-scene duration set by the voiceover stage.
"""

import os
import random
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import httpx

from app.core.celery_app import celery_app
from app.core.config import get_settings
from app.services.supabase import get_supabase_client
from app.services.backblaze import upload_video
from app.services import ffmpeg_scene
from app.schemas.common import RENDER_DIMENSIONS, SCENE_MOTIONS
from app.workers.tasks.project_common import (
    log_event, update_project, get_project, get_scenes, friendly_error,
    is_cancelled, refund_on_final_failure,
)

settings = get_settings()


def _download(url: str, dest: str) -> None:
    with httpx.Client(timeout=120) as client:
        resp = client.get(url)
        resp.raise_for_status()
        with open(dest, "wb") as f:
            f.write(resp.content)


def _motions_for(scene: dict, n_images: int) -> list[str]:
    """Motion(s) for a scene. Mode 1 uses the scene's assigned motion; Mode 2 varies
    motion per image (PRD: per-scene motion randomization)."""
    primary = scene.get("motion_type") or "zoom_in"
    if n_images == 1:
        return [primary]
    rng = random.Random(int(scene.get("seed") or 0))  # deterministic per scene
    return [primary] + [rng.choice(SCENE_MOTIONS) for _ in range(n_images - 1)]


def _render_one(scene: dict, project: dict, tmpdir: str, tail: float = 0.0) -> str:
    """Render a scene clip and return its uploaded URL. `tail` extends the clip so
    scene-to-scene crossfades can consume it without shortening the timeline."""
    w, h = RENDER_DIMENSIONS.get(project["format"], (1080, 1920))
    image_urls = scene.get("image_urls") or []
    if not image_urls:
        raise RuntimeError(f"scene {scene['idx']} has no images")

    local_imgs: list[str] = []
    for i, url in enumerate(image_urls):
        p = os.path.join(tmpdir, f"s{scene['idx']:03d}_{i}.png")
        _download(url, p)
        local_imgs.append(p)

    base = float(scene.get("duration_seconds") or settings.scene_duration_seconds)
    clip_path = os.path.join(tmpdir, f"clip_{scene['idx']:03d}.mp4")
    ffmpeg_scene.render_scene_clip(
        local_imgs, clip_path,
        width=w, height=h,
        duration=base + tail,
        motions=_motions_for(scene, len(local_imgs)),
        fps=settings.scene_render_fps,
        crossfade=settings.scene_crossfade_seconds,
    )
    key = f"projects/{project['user_id']}/{project['id']}/clip_{scene['idx']:03d}.mp4"
    return upload_video(clip_path, key)


@celery_app.task(bind=True, max_retries=2, default_retry_delay=45, queue="media",
                 soft_time_limit=2400, time_limit=2700)
def render_scenes_task(self, project_id: str):
    if is_cancelled(project_id):
        return
    project = get_project(project_id)
    if not project:
        return

    start = time.time()
    client = get_supabase_client()
    tmpdir = tempfile.mkdtemp()
    try:
        scenes = get_scenes(project_id)
        # If crossfades apply, render each clip a touch longer so the transition
        # eats the tail and the total timeline stays synced with the voiceover.
        tail = settings.scene_transition_seconds if settings.scene_transitions_on(len(scenes)) else 0.0

        # Render clips in parallel — FFmpeg runs as a subprocess so it releases the
        # GIL. DB writes stay on this thread (workers only do ffmpeg/download/upload).
        todo = [s for s in scenes if not s.get("clip_url")]  # idempotent on retry
        conc = max(1, min(settings.scene_render_concurrency, len(todo) or 1))
        errors: list[str] = []
        with ThreadPoolExecutor(max_workers=conc) as ex:
            futures = {ex.submit(_render_one, s, project, tmpdir, tail): s for s in todo}
            for fut in as_completed(futures):
                scene = futures[fut]
                try:
                    clip_url = fut.result()
                    client.table("scenes").update(
                        {"clip_url": clip_url, "status": "rendered"}
                    ).eq("id", scene["id"]).execute()
                except Exception as e:  # noqa: BLE001 — collect, fail after all finish
                    errors.append(f"scene {scene['idx']}: {e}")
        if errors:
            raise RuntimeError("; ".join(errors[:5]))

        log_event(project_id, "render", "completed",
                  int((time.time() - start) * 1000), {"scenes": len(scenes)})

        update_project(project_id, {"status": "assembling"})
        from app.workers.tasks.assembly import assemble_task
        assemble_task.apply_async(args=[project_id], queue="media")

    except Exception as exc:
        log_event(project_id, "render", "failed", metadata={"error": str(exc)})
        update_project(project_id, {"status": "failed_at_render",
                                    "error_message": friendly_error("render")})
        if self.request.retries >= self.max_retries:
            refund_on_final_failure(project)
        raise self.retry(exc=exc)
    finally:
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)
