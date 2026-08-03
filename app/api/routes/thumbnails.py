"""Thumbnail Cloner routes (PRD §9, activated 2026-07-24).

- POST /thumbnails/projects/{project_id} — queue 2 auto-generated variations for
  a completed project; the client polls the project until thumbnail_urls fills.
- POST /thumbnails/clone — synchronous: clone a competitor thumbnail from an
  uploaded reference + the user's instructions. Requires the Gemini engine
  (cloning needs the reference image; SDXL never sees it, so no silent fallback).
"""

import asyncio
import re
import uuid

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status

from app.core.config import get_settings
from app.core.deps import CurrentUserId
from app.services.backblaze import upload_bytes
from app.services.ffmpeg_scene import draw_thumbnail_text, to_youtube_thumbnail
from app.services.gemini_image import generate_image_gemini, GeminiCreditsDepletedError
from app.services.supabase import get_supabase_client

router = APIRouter(prefix="/thumbnails", tags=["thumbnails"])
settings = get_settings()

_ALLOWED = {"image/png", "image/jpeg", "image/webp"}
_MAX_BYTES = 8 * 1024 * 1024  # 8 MB

_CLONE_PROMPT = (
    "Recreate this thumbnail's composition, framing, color grading, and dramatic "
    "energy as a NEW original 16:9 YouTube thumbnail. Do not copy it pixel-for-pixel, "
    "and do not carry over its logos, watermarks, or the faces of real people. "
    "Apply these changes from the user: {instructions}. "
    "High contrast, vivid colors, one clear focal point.{text_directive}"
)

# Rendering words correctly is the weak spot of image models: asking for
# "NO JAGUARS" produced "NO JAGJAARS" (Faith, 2026-08-03). Quoting the exact
# string and spelling it out letter by letter is what makes it land.
_TEXT_DIRECTIVE = (
    " Leave the lower third of the frame visually clean and uncluttered so a headline "
    "can be placed there. Render NO text, letters, words, captions, numbers or logos "
    "anywhere in the image."
)


def wanted_headline(instructions: str) -> str:
    """The exact wording the user asked for, taken from quotes in their request.

    Users write the words they want in quotes ("NO JAGUARS"). We render those
    ourselves rather than asking the image model, which cannot spell reliably.
    """
    found = re.findall(r'["\u201c\u2018\']([^"\u201d\u2019\']{1,60})["\u201d\u2019\']', instructions or "")
    found = [w.strip() for w in found if w.strip()]
    return found[0] if found else ""


@router.post("/projects/{project_id}", status_code=status.HTTP_202_ACCEPTED)
async def generate_project_thumbnails(project_id: str, user_id: CurrentUserId):
    """Queue the 2-variation auto-thumbnail job for a completed project."""
    client = get_supabase_client()
    project = (
        client.table("projects").select("id, status, thumbnail_urls")
        .eq("id", project_id).eq("user_id", user_id).single().execute().data
    )
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    if project["status"] != "completed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Thumbnails are generated from a finished video — complete the render first.",
        )
    if project.get("thumbnail_urls"):
        return {"status": "ready", "thumbnail_urls": project["thumbnail_urls"]}

    from app.workers.tasks.thumbnails import generate_thumbnails_task
    generate_thumbnails_task.apply_async(args=[project_id], queue="media")
    return {"status": "queued"}


@router.post("/clone")
async def clone_thumbnail(
    user_id: CurrentUserId,
    file: UploadFile = File(...),
    instructions: str = Form(""),
):
    """Clone a competitor's thumbnail from a reference image (synchronous)."""
    if (file.content_type or "").lower() not in _ALLOWED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Reference must be a PNG, JPG, or WEBP image.",
        )
    data = await file.read()
    if len(data) > _MAX_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Reference image must be under 8 MB.",
        )
    if not (settings.image_provider == "gemini" and settings.google_ai_api_key):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Thumbnail cloning needs the Gemini image engine, which isn't configured.",
        )

    instr = instructions.strip() or "keep the same subject and mood"
    headline = wanted_headline(instr)
    prompt = _CLONE_PROMPT.format(
        instructions=instr, text_directive=_TEXT_DIRECTIVE if headline else "")
    try:
        img = await asyncio.to_thread(
            generate_image_gemini, prompt, "16:9",
            references=[("", data)],
            reference_mime=(file.content_type or "image/png").lower(),
        )
    except GeminiCreditsDepletedError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Gemini image credits are depleted — top up Google AI Studio billing.",
        )
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Thumbnail generation failed. Please try again.",
        )

    # Normalise to YouTube's exact 1280x720, then burn the requested headline so
    # its spelling is exact (the image model gets this wrong).
    img = to_youtube_thumbnail(img)
    if headline:
        img = draw_thumbnail_text(img, headline)
    key = f"thumbnails/{user_id}/{uuid.uuid4().hex}.png"
    url = upload_bytes(img, key, "image/png")
    return {"url": url}
