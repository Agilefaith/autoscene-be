"""Google Gemini (Nano Banana) image generation — AutoScene primary engine.

Generates a scene image from a text prompt and, when provided, a reference image
so the same character stays consistent across scenes. Gemini also renders the
selected art style (Stickman / Cartoon / Ghibli / Family Guy) at high fidelity,
which SDXL cannot. Returns raw image bytes to match stability.generate_image.
"""

from functools import lru_cache

from google import genai
from google.genai import types

from app.core.config import get_settings

settings = get_settings()

# Our formats map 1:1 to Gemini's native aspect ratios (no letterbox needed).
_ASPECT = {"16:9": "16:9", "9:16": "9:16", "1:1": "1:1"}


@lru_cache
def _client() -> genai.Client:
    return genai.Client(api_key=settings.google_ai_api_key)


def generate_image_gemini(
    prompt: str,
    fmt: str = "9:16",
    *,
    reference_bytes: bytes | None = None,
    reference_mime: str = "image/png",
) -> bytes:
    """Generate one image via Gemini. If reference_bytes is given, the subject in
    the reference is kept consistent. Raises on failure (caller may fall back)."""
    contents: list = [prompt]
    if reference_bytes:
        contents.append(types.Part.from_bytes(data=reference_bytes, mime_type=reference_mime))

    config = types.GenerateContentConfig(
        response_modalities=["IMAGE"],
        image_config=types.ImageConfig(aspect_ratio=_ASPECT.get(fmt, "9:16")),
    )
    resp = _client().models.generate_content(
        model=settings.gemini_image_model,
        contents=contents,
        config=config,
    )

    candidates = getattr(resp, "candidates", None) or []
    for cand in candidates:
        parts = getattr(getattr(cand, "content", None), "parts", None) or []
        for part in parts:
            inline = getattr(part, "inline_data", None)
            if inline and getattr(inline, "data", None):
                return inline.data
    raise RuntimeError(f"Gemini returned no image (model={settings.gemini_image_model})")
