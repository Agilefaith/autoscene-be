"""Google Gemini (Nano Banana) image generation — AutoScene primary engine.

Calls the Gemini REST API directly via httpx (no google-genai SDK) to avoid an
httpx version conflict with supabase. Generates a scene image from a text prompt
and, when provided, a reference image so the same character stays consistent
across scenes. Gemini also renders the selected art style (Stickman / Cartoon /
Ghibli / Family Guy) at high fidelity. Returns raw image bytes to match
stability.generate_image (FFmpeg later scales/crops to the exact canvas).
"""

import base64

import httpx

from app.core.config import get_settings

settings = get_settings()

_BASE = "https://generativelanguage.googleapis.com/v1beta/models"


def generate_image_gemini(
    prompt: str,
    fmt: str = "9:16",
    *,
    reference_bytes: bytes | None = None,
    reference_mime: str = "image/png",
) -> bytes:
    """Generate one image via Gemini (synchronous; called from a worker thread).
    If reference_bytes is given, the subject in the reference is kept consistent.
    Raises on failure so the caller can fall back to Stability."""
    parts: list = [{"text": prompt}]
    if reference_bytes:
        parts.append({
            "inline_data": {
                "mime_type": reference_mime,
                "data": base64.b64encode(reference_bytes).decode(),
            }
        })

    body = {
        "contents": [{"parts": parts}],
        "generationConfig": {"responseModalities": ["IMAGE"]},
    }
    url = f"{_BASE}/{settings.gemini_image_model}:generateContent"

    with httpx.Client(timeout=settings.stability_http_timeout_seconds) as client:
        resp = client.post(
            url,
            params={"key": settings.google_ai_api_key},
            headers={"Content-Type": "application/json"},
            json=body,
        )
    if resp.status_code != 200:
        raise RuntimeError(f"Gemini HTTP {resp.status_code}: {resp.text[:300]}")

    data = resp.json()
    for cand in data.get("candidates", []) or []:
        for part in ((cand.get("content") or {}).get("parts") or []):
            inline = part.get("inlineData") or part.get("inline_data")
            if inline and inline.get("data"):
                return base64.b64decode(inline["data"])
    raise RuntimeError(
        f"Gemini returned no image (model={settings.gemini_image_model}): {str(data)[:300]}"
    )
