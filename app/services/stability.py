"""Stability AI (SDXL) image generation — AutoScene Mode 1 & Mode 2.

Verified live against the engine `stable-diffusion-xl-1024-v1-0` (2026-06-27).
SDXL v1 only accepts a fixed set of dimension pairs, so we generate at the closest
pair to the target aspect ratio and let FFmpeg crop/letterbox to the exact canvas.

Reference: docs/AUTOSCENE_PRD.md §7.1 "SDXL Integration Spec".
"""

import base64
import httpx

from app.core.config import get_settings
from app.schemas.common import SDXL_DIMENSIONS

settings = get_settings()


class StabilityError(RuntimeError):
    """Raised on a non-recoverable SDXL failure (the caller decides whether to retry)."""


class ContentFilteredError(StabilityError):
    """SDXL returned `finishReason: CONTENT_FILTERED` — the image is blanked.

    Treated as retriable with a softened prompt rather than a hard pipeline failure.
    """


def _endpoint() -> str:
    return f"{settings.stability_api_host}/v1/generation/{settings.stability_engine_id}/text-to-image"


def _dimensions(fmt: str) -> tuple[int, int]:
    """Closest SDXL-allowed (width, height) pair for an output format."""
    return SDXL_DIMENSIONS.get(fmt, SDXL_DIMENSIONS["9:16"])


async def generate_image(
    prompt: str,
    fmt: str = "9:16",
    *,
    negative_prompt: str | None = None,
    seed: int | None = None,
    style_preset: str | None = None,
    steps: int | None = None,
    cfg_scale: float | None = None,
) -> bytes:
    """Generate one SDXL image and return PNG bytes.

    `fmt` selects the SDXL dimension pair (16:9 / 9:16 / 1:1). `seed` should be
    fixed across a Mode 2 scene's A/B/C frames to lock character/scene identity,
    and varied between scenes. Raises StabilityError / ContentFilteredError so the
    Celery task can retry intelligently.
    """
    if not settings.stability_api_key:
        raise StabilityError("STABILITY_API_KEY is not configured")

    width, height = _dimensions(fmt)
    body: dict = {
        "text_prompts": [
            {"text": prompt, "weight": 1.0},
            {
                "text": negative_prompt or settings.stability_negative_prompt,
                "weight": -1.0,
            },
        ],
        "cfg_scale": cfg_scale if cfg_scale is not None else settings.stability_cfg_scale,
        "height": height,
        "width": width,
        "steps": steps if steps is not None else settings.stability_steps,
        "samples": 1,
        "style_preset": style_preset or settings.stability_style_preset,
    }
    if seed is not None:
        # SDXL seeds are uint32; keep it in range so a fixed seed stays reproducible.
        body["seed"] = int(seed) % 4294967295

    headers = {
        "Authorization": f"Bearer {settings.stability_api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    async with httpx.AsyncClient(timeout=settings.stability_http_timeout_seconds) as client:
        resp = await client.post(_endpoint(), headers=headers, json=body)

    if resp.status_code != 200:
        raise StabilityError(
            f"SDXL generation failed (HTTP {resp.status_code}): {resp.text[:300]}"
        )

    artifacts = (resp.json() or {}).get("artifacts") or []
    if not artifacts:
        raise StabilityError("SDXL returned no artifacts")

    art = artifacts[0]
    finish = art.get("finishReason")
    if finish == "CONTENT_FILTERED":
        raise ContentFilteredError("SDXL content filter blanked the image")
    if finish != "SUCCESS":
        raise StabilityError(f"SDXL finishReason={finish!r}")

    return base64.b64decode(art["base64"])


async def account_balance() -> float:
    """Return remaining Stability credits (for health checks / admin)."""
    headers = {"Authorization": f"Bearer {settings.stability_api_key}"}
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(f"{settings.stability_api_host}/v1/user/balance", headers=headers)
    resp.raise_for_status()
    return float(resp.json().get("credits", 0))
