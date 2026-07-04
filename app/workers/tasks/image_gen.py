"""SDXL image generation stage (AutoScene Mode 1 & 2).

Mode 1 → one image per scene. Mode 2 → three images (A/B/C) sharing the scene's
seed so character/scene identity stays locked across the progression. Images are
generated concurrently (bounded) per PRD §8 "batch image generation".
"""

import asyncio
import time

from app.core.celery_app import celery_app
from app.core.config import get_settings
from app.services.supabase import get_supabase_client
from app.services.stability import generate_image, ContentFilteredError
from app.services.backblaze import upload_bytes
from app.workers.tasks.project_common import (
    log_event, update_project, get_project, get_scenes, friendly_error,
    is_cancelled, refund_on_final_failure,
)

settings = get_settings()

# Cap concurrent SDXL calls so we don't hammer the API / exhaust workers.
_MAX_CONCURRENCY = 4


def _prompts_for(scene: dict, render_mode: str) -> list[str]:
    """The prompt list to render for a scene: [base] for Mode 1, [A,B,C] for Mode 2."""
    if render_mode == "mode_2":
        prompts = scene.get("image_prompts") or []
        if len(prompts) >= 3:
            return prompts[:3]
        base = scene.get("image_prompt") or ""
        return (prompts + [base] * 3)[:3]
    return [scene.get("image_prompt") or ""]


async def _gen_one(prompt: str, fmt: str, style_preset: str | None, seed: int) -> bytes:
    """Generate a single image, retrying past the content filter with a fresh seed."""
    try:
        return await generate_image(prompt, fmt, seed=seed, style_preset=style_preset)
    except ContentFilteredError:
        # Softened retry: different seed often clears a spurious filter hit.
        return await generate_image(prompt, fmt, seed=seed + 7, style_preset=style_preset)


async def _generate_scene_images(scene: dict, project: dict, sem: asyncio.Semaphore) -> list[str]:
    """Generate + upload all images for one scene. Returns the list of public URLs."""
    fmt = project["format"]
    style_preset = project.get("style") or None
    seed = int(scene.get("seed") or 0)
    prompts = _prompts_for(scene, project["render_mode"])

    urls: list[str] = []
    for i, prompt in enumerate(prompts):
        async with sem:
            img = await _gen_one(prompt, fmt, style_preset, seed + i if project["render_mode"] == "mode_2" else seed)
        # Mode 2 frames share the seed family (seed, seed+1, seed+2) for continuity
        # with slight variation; Mode 1 uses the scene seed directly.
        key = f"projects/{project['user_id']}/{project['id']}/scene_{scene['idx']:03d}_{i}.png"
        urls.append(upload_bytes(img, key, "image/png"))
    return urls


async def _generate_all(project: dict, scenes: list[dict]) -> dict[str, list[str]]:
    sem = asyncio.Semaphore(_MAX_CONCURRENCY)
    results = await asyncio.gather(
        *[_generate_scene_images(s, project, sem) for s in scenes],
        return_exceptions=True,
    )
    out: dict[str, list[str]] = {}
    errors: list[str] = []
    for scene, res in zip(scenes, results):
        if isinstance(res, Exception):
            errors.append(f"scene {scene['idx']}: {res}")
        else:
            out[scene["id"]] = res
    if errors:
        raise RuntimeError("; ".join(errors[:5]))
    return out


@celery_app.task(bind=True, max_retries=2, default_retry_delay=45, queue="media")
def generate_images_task(self, project_id: str):
    if is_cancelled(project_id):
        return
    project = get_project(project_id)
    if not project:
        return

    start = time.time()
    client = get_supabase_client()
    try:
        # Only (re)generate scenes that don't yet have images — makes retries cheap.
        scenes = [s for s in get_scenes(project_id) if not s.get("image_urls")]
        n_images = 0
        if scenes:
            urls_by_scene = asyncio.run(_generate_all(project, scenes))
            for scene_id, urls in urls_by_scene.items():
                n_images += len(urls)
                client.table("scenes").update(
                    {"image_urls": urls, "status": "image_ready"}
                ).eq("id", scene_id).execute()

        # Track SDXL spend (biggest variable cost) for cost monitoring.
        from app.services import metrics
        metrics.log_metric(project_id, "images",
                           int((time.time() - start) * 1000), metrics.sdxl_cost(n_images))
        log_event(project_id, "images", "completed",
                  int((time.time() - start) * 1000),
                  {"scenes": len(scenes), "images": n_images, "mode": project["render_mode"]})

        update_project(project_id, {"status": "voiceover"})
        from app.workers.tasks.voiceover import generate_voiceover_task
        generate_voiceover_task.apply_async(args=[project_id], queue="media")

    except Exception as exc:
        log_event(project_id, "images", "failed", metadata={"error": str(exc)})
        update_project(project_id, {"status": "failed_at_images",
                                    "error_message": friendly_error("images")})
        if self.request.retries >= self.max_retries:
            refund_on_final_failure(project)
        raise self.retry(exc=exc)


@celery_app.task(bind=True, max_retries=2, default_retry_delay=30, queue="media")
def regenerate_single_scene(self, scene_id: str):
    """Regenerate images for one scene (Scenes-preview "regenerate" action)."""
    client = get_supabase_client()
    scene = client.table("scenes").select("*").eq("id", scene_id).single().execute().data
    if not scene:
        return
    project = get_project(scene["project_id"])
    if not project:
        return
    try:
        sem = asyncio.Semaphore(_MAX_CONCURRENCY)
        urls = asyncio.run(_generate_scene_images({**scene, "image_urls": None}, project, sem))
        client.table("scenes").update(
            {"image_urls": urls, "status": "image_ready", "error_message": None}
        ).eq("id", scene_id).execute()
    except Exception as exc:
        client.table("scenes").update(
            {"status": "failed", "error_message": str(exc)[:300]}
        ).eq("id", scene_id).execute()
        raise self.retry(exc=exc)
