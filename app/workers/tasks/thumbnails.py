"""Thumbnail generation (PRD §9 "Thumbnail Cloner", activated 2026-07-24).

Two variations are generated per completed project: GPT writes two contrasting
click-worthy thumbnail prompts from the project's script/niche/style, then the
image engine renders them at 16:9 (Gemini primary, SDXL fallback — same path as
scene images). URLs land in projects.thumbnail_urls.

The "clone a competitor thumbnail" flow is synchronous in the API route
(app/api/routes/thumbnails.py) — single image, user is waiting.
"""

import asyncio
import json
import time

from openai import AsyncOpenAI

from app.core.celery_app import celery_app
from app.core.config import get_settings
from app.services.supabase import get_supabase_client
from app.services.backblaze import upload_bytes
from app.schemas.common import style_prompt, style_sdxl_preset, style_negative
from app.workers.tasks.image_gen import _gen_one
from app.workers.tasks.project_common import log_event, get_project

settings = get_settings()
_client = AsyncOpenAI(api_key=settings.openai_api_key)

# YouTube-native thumbnail canvas; SDXL renders 1344x768 and FFmpeg-less usage
# is fine since thumbnails are standalone images.
_THUMB_FORMAT = "16:9"

_PROMPT_SYSTEM = (
    "You are a YouTube thumbnail art director. Given a video script summary, "
    "write TWO contrasting text-to-image prompts for click-worthy 16:9 thumbnails "
    "of that video. Each prompt: one paragraph — main subject with an expressive "
    "emotional face or striking focal object, bold composition with a clear focal "
    "point, high contrast dramatic lighting, vivid colors, room for title text on "
    "one side. No text, captions, logos, or watermarks in the image itself. "
    'Output JSON only: {"prompts": [str, str]}'
)


async def _thumbnail_prompts(script_excerpt: str, niche: str, style_text: str) -> list[str]:
    resp = await _client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": _PROMPT_SYSTEM},
            {"role": "user", "content": (
                f"Niche: {niche or 'general'}.\n"
                f"Script (excerpt):\n{script_excerpt[:1500]}\n\n"
                "Write the two thumbnail prompts."
            )},
        ],
        temperature=0.8,
        max_tokens=600,
        response_format={"type": "json_object"},
    )
    data = json.loads(resp.choices[0].message.content or "{}")
    prompts = [p for p in (data.get("prompts") or []) if p][:2]
    # Same style block placement as scene prompts: description first, style last.
    if style_text:
        prompts = [f"{p.rstrip('.')}. {style_text}" for p in prompts]
    return prompts


async def _generate(project: dict, script_text: str) -> list[str]:
    style_id = project.get("style") or ""
    prompts = await _thumbnail_prompts(script_text, project.get("niche") or "", style_prompt(style_id))
    if not prompts:
        raise RuntimeError("No thumbnail prompts produced")

    urls: list[str] = []
    for i, prompt in enumerate(prompts):
        img, _engine, _reason = await _gen_one(
            prompt, _THUMB_FORMAT, style_sdxl_preset(style_id),
            seed=9000 + i, cast=[], negative=style_negative(style_id),
        )
        key = f"projects/{project['user_id']}/{project['id']}/thumb_{i}.png"
        urls.append(upload_bytes(img, key, "image/png"))
    return urls


@celery_app.task(bind=True, max_retries=2, default_retry_delay=30, queue="media",
                 soft_time_limit=600, time_limit=700)
def generate_thumbnails_task(self, project_id: str):
    """Generate 2 thumbnail variations for a completed project (idempotent)."""
    project = get_project(project_id)
    if not project:
        return
    if project.get("thumbnail_urls"):
        return  # already generated

    start = time.time()
    client = get_supabase_client()
    try:
        script = (
            client.table("scripts").select("content")
            .eq("id", project["script_id"]).single().execute().data
        )
        text = (script or {}).get("content") or project.get("name") or ""
        urls = asyncio.run(_generate(project, text))
        client.table("projects").update({"thumbnail_urls": urls}).eq("id", project_id).execute()
        log_event(project_id, "thumbnails", "completed",
                  int((time.time() - start) * 1000), {"count": len(urls)})
    except Exception as exc:
        log_event(project_id, "thumbnails", "failed", metadata={"error": str(exc)[:300]})
        raise self.retry(exc=exc)
