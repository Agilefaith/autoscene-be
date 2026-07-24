"""SDXL image generation stage (AutoScene Mode 1 & 2).

Mode 1 → one image per scene. Mode 2 → three images (A/B/C) sharing the scene's
seed so character/scene identity stays locked across the progression. Images are
generated concurrently (bounded) per PRD §8 "batch image generation".
"""

import asyncio
import time

import httpx

from app.core.celery_app import celery_app
from app.core.config import get_settings
from app.services.supabase import get_supabase_client
from app.services.stability import generate_image, ContentFilteredError
from app.services.gemini_image import generate_image_gemini, GeminiCreditsDepletedError
from app.services.backblaze import upload_bytes
from app.schemas.common import style_sdxl_preset, style_negative
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


def _fetch_reference(url: str | None) -> bytes | None:
    """Download the project's reference image once (for Gemini character lock)."""
    if not url:
        return None
    try:
        with httpx.Client(timeout=60) as c:
            r = c.get(url)
            r.raise_for_status()
            return r.content
    except httpx.HTTPError:
        return None


def _fetch_cast(project: dict) -> list[tuple[str, bytes]]:
    """Download every named character's reference image once per render, so each
    generated scene can be conditioned on the whole cast. Falls back to the
    legacy single `reference_image_url` for pre-2026-07-21 projects."""
    characters = [c for c in (project.get("characters") or []) if c.get("image_url")]
    if not characters and project.get("reference_image_url"):
        characters = [{"name": "", "image_url": project["reference_image_url"]}]
    cast: list[tuple[str, bytes]] = []
    for c in characters:
        data = _fetch_reference(c["image_url"])
        if data:
            cast.append((c.get("name") or "", data))
    return cast


async def _gen_one(prompt: str, fmt: str, sdxl_preset: str | None, seed: int,
                   cast: list[tuple[str, bytes]],
                   negative: str | None = None) -> tuple[bytes, str, str | None]:
    """Generate one image. Gemini is primary (character lock via the cast's
    reference images + high style fidelity); Stability SDXL is the fallback.
    Style is already baked into the prompt, so Gemini needs no preset.

    Returns (image_bytes, engine, fallback_reason). Depleted Gemini credits do
    NOT fall back — every image would silently degrade (no reference, weaker
    style), so the render fails loudly instead."""
    fallback_reason: str | None = None
    if settings.image_provider == "gemini" and settings.google_ai_api_key:
        try:
            img = await asyncio.to_thread(
                generate_image_gemini, prompt, fmt, references=cast
            )
            return img, "gemini", None
        except GeminiCreditsDepletedError:
            raise
        except Exception as exc:
            fallback_reason = str(exc)[:200]  # fall through to Stability
    try:
        img = await generate_image(prompt, fmt, seed=seed, style_preset=sdxl_preset,
                                   negative_prompt=negative)
    except ContentFilteredError:
        img = await generate_image(prompt, fmt, seed=seed + 7, style_preset=sdxl_preset,
                                   negative_prompt=negative)
    return img, "sdxl", fallback_reason


async def _generate_scene_images(scene: dict, project: dict, sem: asyncio.Semaphore,
                                 cast: list[tuple[str, bytes]]) -> tuple[list[str], list[dict]]:
    """Generate + upload all images for one scene. Returns (public URLs, one
    {engine, fallback_reason} record per image)."""
    fmt = project["format"]
    # Preset + negative prompt are only used by the Stability fallback.
    sdxl_preset = style_sdxl_preset(project.get("style") or "")
    negative = style_negative(project.get("style") or "")
    seed = int(scene.get("seed") or 0)
    prompts = _prompts_for(scene, project["render_mode"])

    urls: list[str] = []
    engines: list[dict] = []
    for i, prompt in enumerate(prompts):
        async with sem:
            img, engine, fallback_reason = await _gen_one(
                prompt, fmt, sdxl_preset,
                seed + i if project["render_mode"] == "mode_2" else seed,
                cast, negative)
        engines.append({"engine": engine, "fallback_reason": fallback_reason})
        key = f"projects/{project['user_id']}/{project['id']}/scene_{scene['idx']:03d}_{i}.png"
        urls.append(upload_bytes(img, key, "image/png"))
    return urls, engines


async def _generate_all(project: dict, scenes: list[dict]) -> tuple[dict[str, list[str]], dict]:
    """Returns (urls per scene id, engine stats). Engine stats:
    {"gemini": n, "sdxl": n, "fallback_reasons": [up to 3 distinct reasons]}."""
    sem = asyncio.Semaphore(_MAX_CONCURRENCY)
    cast = await asyncio.to_thread(_fetch_cast, project)
    results = await asyncio.gather(
        *[_generate_scene_images(s, project, sem, cast) for s in scenes],
        return_exceptions=True,
    )
    out: dict[str, list[str]] = {}
    stats: dict = {"gemini": 0, "sdxl": 0, "fallback_reasons": []}
    errors: list[str] = []
    for scene, res in zip(scenes, results):
        if isinstance(res, GeminiCreditsDepletedError):
            raise res  # terminal: surface the clear message, don't mask it
        if isinstance(res, BaseException):
            errors.append(f"scene {scene['idx']}: {res}")
            continue
        urls, engines = res
        out[scene["id"]] = urls
        for e in engines:
            stats[e["engine"]] = stats.get(e["engine"], 0) + 1
            reason = e.get("fallback_reason")
            if reason and reason not in stats["fallback_reasons"] and len(stats["fallback_reasons"]) < 3:
                stats["fallback_reasons"].append(reason)
    if errors:
        raise RuntimeError("; ".join(errors[:5]))
    return out, stats


@celery_app.task(bind=True, max_retries=2, default_retry_delay=45, queue="media",
                 soft_time_limit=2400, time_limit=2700)
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
        engine_stats: dict = {}
        if scenes:
            urls_by_scene, engine_stats = asyncio.run(_generate_all(project, scenes))
            for scene_id, urls in urls_by_scene.items():
                n_images += len(urls)
                client.table("scenes").update(
                    {"image_urls": urls, "status": "image_ready"}
                ).eq("id", scene_id).execute()

        # A fallback while Gemini is the configured provider means degraded output
        # (no reference image, weaker style) — make it visible in job_events.
        if settings.image_provider == "gemini" and engine_stats.get("sdxl"):
            log_event(project_id, "images", "warning", metadata={
                "message": "some images fell back to SDXL (no character reference applied)",
                **engine_stats,
            })

        # Track SDXL spend (biggest variable cost) for cost monitoring.
        from app.services import metrics
        metrics.log_metric(project_id, "images",
                           int((time.time() - start) * 1000), metrics.sdxl_cost(n_images))
        log_event(project_id, "images", "completed",
                  int((time.time() - start) * 1000),
                  {"scenes": len(scenes), "images": n_images,
                   "mode": project["render_mode"], **engine_stats})

        update_project(project_id, {"status": "voiceover"})
        from app.workers.tasks.voiceover import generate_voiceover_task
        generate_voiceover_task.apply_async(args=[project_id], queue="media")

    except GeminiCreditsDepletedError as exc:
        # Terminal, not transient: retrying can't help until billing is topped up,
        # and falling back would silently degrade the whole video.
        log_event(project_id, "images", "failed",
                  metadata={"error": str(exc), "reason": "gemini_credits_depleted"})
        update_project(project_id, {
            "status": "failed_at_images",
            "error_message": ("Image generation is paused: the Gemini image credits are "
                              "depleted. Top up Google AI Studio billing, then retry."),
        })
        refund_on_final_failure(project)
        return

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
        cast = _fetch_cast(project)
        urls, _ = asyncio.run(_generate_scene_images({**scene, "image_urls": None}, project, sem, cast))
        client.table("scenes").update(
            {"image_urls": urls, "status": "image_ready", "error_message": None}
        ).eq("id", scene_id).execute()
    except GeminiCreditsDepletedError:
        client.table("scenes").update(
            {"status": "failed",
             "error_message": "Gemini image credits are depleted — top up Google AI Studio billing."}
        ).eq("id", scene_id).execute()
        return
    except Exception as exc:
        client.table("scenes").update(
            {"status": "failed", "error_message": str(exc)[:300]}
        ).eq("id", scene_id).execute()
        raise self.retry(exc=exc)
