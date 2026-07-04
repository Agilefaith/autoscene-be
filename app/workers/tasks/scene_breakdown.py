"""Scene breakdown task + full-pipeline orchestrator (AutoScene)."""

import asyncio
import random
import time

from app.core.celery_app import celery_app
from app.services.supabase import get_supabase_client
from app.services.scene_engine import breakdown_script
from app.schemas.common import motion_for_emotion
from app.workers.tasks.project_common import (
    log_event, update_project, get_project, friendly_error, is_cancelled,
)


def _build_scene_rows(project: dict, scenes: list[dict]) -> list[dict]:
    """Attach per-scene generation params (seed + motion) and FK columns."""
    # ONE seed shared by every scene (best-effort character consistency): the same
    # noise anchor plus the reused character description keeps SDXL output as close
    # as its text-to-image API allows. Mode 2 varies within a scene via seed+i.
    project_seed = random.randint(1, 2_000_000_000)
    rows = []
    for s in scenes:
        # Camera move chosen to fit the scene's emotion (deterministic per scene).
        rng = random.Random(project_seed + s["idx"])
        rows.append({
            "project_id": project["id"],
            "user_id": project["user_id"],
            "idx": s["idx"],
            "scene_text": s.get("scene_text"),
            "emotion": s.get("emotion"),
            "action": s.get("action"),
            "environment": s.get("environment"),
            "image_prompt": s.get("image_prompt"),
            "image_prompts": s.get("image_prompts"),
            "seed": project_seed,
            "motion_type": motion_for_emotion(s.get("emotion") or "", rng),
            "duration_seconds": s.get("duration_seconds", 10),
            "status": "prompted",
        })
    return rows


def _run_breakdown(project_id: str) -> int:
    """Core: load script, break into scenes, replace the project's scenes. Returns
    the scene count. Raises on failure."""
    client = get_supabase_client()
    project = get_project(project_id)
    if not project:
        return 0

    script = (
        client.table("scripts").select("content")
        .eq("id", project["script_id"]).single().execute().data
    )
    content = (script or {}).get("content") or ""
    if not content.strip():
        raise ValueError("Project script is empty")

    # Best-effort character consistency: build a fixed character description from
    # the reference image once (via GPT-Vision) and reuse it across every scene.
    character_desc = project.get("character_desc") or ""
    ref_url = project.get("reference_image_url")
    if ref_url and not character_desc:
        from app.services.openai_service import describe_reference_image
        character_desc = asyncio.run(describe_reference_image(ref_url))
        if character_desc:
            client.table("projects").update(
                {"character_desc": character_desc}
            ).eq("id", project_id).execute()

    scenes = asyncio.run(breakdown_script(
        content,
        render_mode=project["render_mode"],
        niche=project.get("niche") or "",
        style=project.get("style") or "",
        character_desc=character_desc,
        duration_seconds=project["duration_seconds"],
        scene_duration=project.get("scene_duration_seconds"),
    ))
    if not scenes:
        raise ValueError("Scene breakdown produced no scenes")

    # Replace any prior scenes (e.g. re-running breakdown after an edit).
    client.table("scenes").delete().eq("project_id", project_id).execute()
    client.table("scenes").insert(_build_scene_rows(project, scenes)).execute()
    return len(scenes)


@celery_app.task(bind=True, max_retries=3, default_retry_delay=30, queue="fast")
def run_scene_breakdown(self, project_id: str):
    """Standalone breakdown for the Scenes step (no credits, no full render)."""
    if is_cancelled(project_id):
        return
    start = time.time()
    try:
        count = _run_breakdown(project_id)
        update_project(project_id, {"status": "scenes_ready"})
        log_event(project_id, "breakdown", "completed",
                  int((time.time() - start) * 1000), {"scene_count": count})
    except Exception as exc:
        log_event(project_id, "breakdown", "failed", metadata={"error": str(exc)})
        update_project(project_id, {"status": "failed_at_breakdown",
                                    "error_message": friendly_error("breakdown")})
        raise self.retry(exc=exc)


@celery_app.task(bind=True, max_retries=3, default_retry_delay=30, queue="fast")
def run_project_pipeline(self, project_id: str):
    """Full render orchestrator entry. Scenes are expected to exist already (the
    /generate route enforces it); we (re)ensure them for safety, then hand off to
    the image-generation stage. Each stage dispatches the next."""
    project = get_project(project_id)
    if not project or project.get("status") == "cancelled":
        return

    try:
        # Safety net: if scenes were cleared, rebuild them before generating images.
        existing = (
            get_supabase_client().table("scenes").select("id", count="exact")
            .eq("project_id", project_id).execute().count or 0
        )
        if existing == 0:
            _run_breakdown(project_id)

        update_project(project_id, {"status": "generating_images"})
        from app.workers.tasks.image_gen import generate_images_task
        generate_images_task.apply_async(args=[project_id], queue="media")
    except Exception as exc:
        log_event(project_id, "breakdown", "failed", metadata={"error": str(exc)})
        update_project(project_id, {"status": "failed_at_breakdown",
                                    "error_message": friendly_error("breakdown")})
        from app.workers.tasks.project_common import refund_on_final_failure
        if self.request.retries >= self.max_retries:
            refund_on_final_failure(project)
        raise self.retry(exc=exc)
