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
SDXL_USD_PER_IMAGE          = 0.01    # SDXL v1 (~30 steps) — the biggest variable cost


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
    """Estimated SDXL image-generation spend for a project (the biggest variable
    cost, and the one that dominates long/Mode-2 videos)."""
    return round(max(0, n_images) * SDXL_USD_PER_IMAGE, 4)
