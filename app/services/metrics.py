"""Per-stage pipeline metrics + rough API cost estimates.

Writes to the `pipeline_metrics` table (stage, duration_ms, api_cost_estimate) so
spend can be monitored per project/stage. Costs are ESTIMATES based on public rates;
they are for monitoring, not billing.
"""
from app.services.supabase import get_supabase_client

# Public-rate estimates (USD). Adjust here if provider pricing changes.
ELEVENLABS_USD_PER_1K_CHARS = 0.05    # eleven_flash_v2_5 (~half of multilingual_v2)
OPENAI_USD_PER_1K_TOKENS    = 0.0006  # gpt-4o-mini blended in/out
WHISPER_USD_PER_MIN         = 0.006
SDXL_USD_PER_IMAGE          = 0.01    # SDXL v1 (~30 steps)
# Gemini 2.5 Flash Image: measured at 1290 output tokens per image (verified live
# against our own 9:16 renders) at the published $30 / 1M output tokens. It is ~5x
# SDXL and is the dominant cost of any long video, so it must not be reported as
# SDXL pricing.
GEMINI_USD_PER_IMAGE        = 0.0387


def log_metric(project_id: str, stage: str, duration_ms: int | None = None,
               api_cost_estimate: float | None = None) -> None:
    """Insert one pipeline_metrics row. Never raises into the pipeline."""
    try:
        get_supabase_client().table("pipeline_metrics").insert({
            "project_id": project_id,
            "stage": stage,
            "duration_ms": duration_ms,
            "api_cost_estimate": api_cost_estimate,
        }).execute()
    except Exception:
        pass  # metrics must never break the pipeline


def tts_cost(text: str) -> float:
    return round(len(text or "") / 1000 * ELEVENLABS_USD_PER_1K_CHARS, 4)


def openai_cost_from_text(*texts: str) -> float:
    """Estimate OpenAI cost from text length (~4 chars/token)."""
    chars = sum(len(t or "") for t in texts)
    tokens = chars / 4
    return round(tokens / 1000 * OPENAI_USD_PER_1K_TOKENS, 4)


def whisper_cost(duration_seconds: float) -> float:
    return round(max(0.0, duration_seconds) / 60 * WHISPER_USD_PER_MIN, 4)


def sdxl_cost(n_images: int) -> float:
    """Estimated SDXL image-generation spend for a project."""
    return round(max(0, n_images) * SDXL_USD_PER_IMAGE, 4)


def image_cost(engine_counts: dict) -> float:
    """Image-generation spend for a project, priced per engine.

    `engine_counts` is the {"gemini": n, "sdxl": n} tally the image stage already
    collects. Pricing per engine matters: Gemini is ~5x SDXL and dominates the cost
    of any long video, so billing every image at SDXL rates under-reported spend."""
    gemini = max(0, int(engine_counts.get("gemini") or 0))
    sdxl = max(0, int(engine_counts.get("sdxl") or 0))
    return round(gemini * GEMINI_USD_PER_IMAGE + sdxl * SDXL_USD_PER_IMAGE, 4)
