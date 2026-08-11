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


class GeminiCreditsDepletedError(RuntimeError):
    """The Gemini API key's prepaid credits / quota are exhausted (HTTP 429
    RESOURCE_EXHAUSTED). Falling back silently would degrade every image in the
    video, so callers should fail loudly instead."""


def generate_image_gemini(
    prompt: str,
    fmt: str = "9:16",
    *,
    reference_bytes: bytes | None = None,
    references: list[tuple[str, bytes]] | list[tuple[str, bytes, str]] | None = None,
    reference_mime: str = "image/png",
) -> bytes:
    """Generate one image via Gemini (synchronous; called from a worker thread).

    `references` is the project's named cast as [(name, image_bytes)] — each one
    is attached with a label so Gemini knows which face belongs to which name in
    the prompt. `reference_bytes` is the legacy single-reference form.

    Each entry may optionally carry its own mime type as a 3rd tuple element
    — (name, bytes, mime) — for callers mixing upload formats (e.g. the
    thumbnail cloner's reference + avatar, which can each be png/jpeg/webp).
    Entries without one fall back to `reference_mime`, unchanged from before.

    Raises on failure so the caller can fall back to Stability."""
    parts: list = [{"text": prompt}]
    for ref in (references or []):
        name, data = ref[0], ref[1]
        mime = ref[2] if len(ref) > 2 else reference_mime
        if name:
            parts.append({"text": f"Reference image for {name} — keep this character's "
                                  f"face, hair, skin tone, and build identical:"})
        parts.append({
            "inline_data": {
                "mime_type": mime,
                "data": base64.b64encode(data).decode(),
            }
        })
    if reference_bytes and not references:
        parts.append({
            "inline_data": {
                "mime_type": reference_mime,
                "data": base64.b64encode(reference_bytes).decode(),
            }
        })

    body = {
        "contents": [{"parts": parts}],
        "generationConfig": {
            "responseModalities": ["IMAGE"],
            "imageConfig": {"aspectRatio": fmt},
        },
    }
    url = f"{_BASE}/{settings.gemini_image_model}:generateContent"

    with httpx.Client(timeout=settings.stability_http_timeout_seconds) as client:
        resp = client.post(
            url,
            params={"key": settings.google_ai_api_key},
            headers={"Content-Type": "application/json"},
            json=body,
        )
    # Only the depleted-balance 429 is terminal; per-minute rate-limit 429s are
    # transient and must keep falling through to the caller's normal handling.
    if resp.status_code == 429 and (
        "depleted" in resp.text or "prepayment" in resp.text
    ):
        raise GeminiCreditsDepletedError(f"Gemini HTTP 429: {resp.text[:300]}")
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
