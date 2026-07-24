from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache
from dataclasses import dataclass


@dataclass(frozen=True)
class Plan:
    """A subscription plan (config-driven). Quota is by NUMBER OF VIDEOS per
    month plus a MAX DURATION per video and the render modes it unlocks. Hard
    limits, no rollover, monthly reset — per the AutoScene billing model."""
    id: str
    name: str
    price_usd: int
    videos_per_month: int
    max_duration_seconds: int
    allowed_modes: tuple[str, ...]
    variant_env: str  # Settings attr holding the Lemon Squeezy variant id ("" = free)


# The AutoScene plan catalog (config-driven). Prices/quotas/caps mirror the
# agreed pricing structure; Lemon Squeezy variant ids come from the environment.
PLANS: dict[str, "Plan"] = {
    "free":       Plan("free", "Free Trial", 0, 1, 30, ("mode_1",), ""),
    "starter":    Plan("starter", "Starter", 7, 10, 900, ("mode_1",), "lemonsqueezy_starter_variant_id"),
    "creator":    Plan("creator", "Creator", 18, 30, 1200, ("mode_1",), "lemonsqueezy_creator_variant_id"),
    "scale":      Plan("scale", "Scale", 28, 65, 1800, ("mode_1",), "lemonsqueezy_scale_variant_id"),
    "creator_m2": Plan("creator_m2", "Creator Mode 2", 25, 20, 1200, ("mode_1", "mode_2"), "lemonsqueezy_creator_m2_variant_id"),
    "scale_m2":   Plan("scale_m2", "Scale Mode 2", 47, 50, 1500, ("mode_1", "mode_2"), "lemonsqueezy_scale_m2_variant_id"),
}
# Paid plans that get premium-level rate limits / concurrency.
_PREMIUM_PLANS = {"scale", "scale_m2"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── App ───────────────────────────────────────────────────────────────────
    environment: str = "development"
    debug: bool = True
    cors_origins: list[str] = ["http://localhost:3000", "https://autoscene.app"]

    # ── Sentry (exception tracking) ───────────────────────────────────────────
    # Leave empty to disable. Set SENTRY_DSN in the environment to enable.
    sentry_dsn: str = ""
    sentry_traces_sample_rate: float = 0.1

    # ── Supabase ──────────────────────────────────────────────────────────────
    supabase_url: str
    supabase_service_role_key: str
    supabase_jwt_secret: str
    supabase_anon_key: str

    # ── At-rest encryption (per-user voice API keys) ──────────────────────────
    # Optional: when empty, the Fernet key is derived from supabase_jwt_secret
    # (see app/services/crypto.py). Set explicitly to rotate independently.
    app_encryption_key: str = ""

    # ── Redis / Celery ────────────────────────────────────────────────────────
    redis_url: str = "redis://localhost:6379/0"

    # ── AI APIs ───────────────────────────────────────────────────────────────
    openai_api_key: str
    elevenlabs_api_key: str
    minimax_api_key: str = ""
    # Flash is ~3-4x faster and ~3.6x cheaper than multilingual_v2 (the biggest
    # cost + latency lever in the pipeline). Config-driven so it stays swappable.
    elevenlabs_model: str = "eleven_flash_v2_5"

    # ── Image provider (config-driven; Gemini primary, Stability fallback) ─────
    # "gemini" (Google Nano Banana): native character consistency from a reference
    # image + high style fidelity (Ghibli/Family Guy). "stability": SDXL fallback.
    image_provider: str = "gemini"
    google_ai_api_key: str = ""                # Faith's Google AI Studio key (.env only)
    gemini_image_model: str = "gemini-2.5-flash-image"
    gemini_http_timeout_seconds: int = 120

    # ── Stability AI (SDXL) — fallback image engine ────────────────────────────
    # Verified live 2026-06-27. Engine list returns only stable-diffusion-xl-1024-v1-0.
    stability_api_key: str = ""
    stability_api_host: str = "https://api.stability.ai"
    stability_engine_id: str = "stable-diffusion-xl-1024-v1-0"
    stability_steps: int = 40                 # 10–50; quality/cost balance (40 = sharper)
    stability_cfg_scale: float = 7.0          # prompt adherence (1–35)
    stability_style_preset: str = "cinematic"  # default visual style preset
    stability_http_timeout_seconds: int = 120
    stability_negative_prompt: str = (
        "blurry, low quality, deformed, disfigured, extra limbs, watermark, "
        "text, signature, jpeg artifacts, out of frame"
    )

    # ── Backblaze B2 ──────────────────────────────────────────────────────────
    backblaze_key_id: str
    backblaze_application_key: str
    backblaze_bucket_name: str
    backblaze_bucket_id: str
    backblaze_endpoint_url: str
    backblaze_region: str

    # ── Lemon Squeezy ─────────────────────────────────────────────────────────
    lemonsqueezy_api_key: str = ""
    lemonsqueezy_store_id: str = ""
    lemonsqueezy_webhook_secret: str = ""
    # Subscription variant ids (create one monthly product per plan in LS).
    lemonsqueezy_starter_variant_id: str = ""
    lemonsqueezy_creator_variant_id: str = ""
    lemonsqueezy_scale_variant_id: str = ""
    lemonsqueezy_creator_m2_variant_id: str = ""
    lemonsqueezy_scale_m2_variant_id: str = ""

    # ── Billing / quota config (config-driven) ────────────────────────────────
    # Billing is a per-month VIDEO QUOTA (see PLANS). Each generated video costs
    # 1 quota unit regardless of duration/mode (duration + mode are gated per
    # plan instead). The multiplier below is kept only for the internal COST
    # metric (Mode 2 renders ~3x the images of Mode 1).
    credit_seconds_per_unit: int = 30
    trial_initial_credits: int = 1
    mode_1_credit_multiplier: int = 1
    mode_2_credit_multiplier: int = 3          # internal cost metric only

    # ── Script generation (config-driven) ─────────────────────────────────────
    # Spoken delivery rate: sizes generated scripts AND estimates how long an
    # arbitrary script reads aloud. Single source of truth for WPM.
    script_words_per_minute: int = 130
    script_gen_model: str = "gpt-4o-mini"
    # Above this target duration a single completion can't reliably fill the word
    # count, so generation switches to an outline→expand multi-call strategy.
    script_chunk_threshold_seconds: int = 180
    # Approx words each expanded section (beat) targets in the multi-call path.
    script_words_per_section: int = 220
    # Max follow-up "continue" calls when the assembled script is under the floor.
    script_max_floor_iterations: int = 2

    # ── AutoScene scene defaults (config-driven) ──────────────────────────────
    scene_duration_seconds: int = 10          # PRD / Faith: split script into ~10s scenes
    scene_min_count: int = 1
    scene_max_count: int = 240                 # supports up to 40-min videos
    # Max scenes requested per OpenAI breakdown call. Long scripts are split into
    # several chunks so one response never overflows the model's output-token cap
    # (which truncates the JSON → "Unterminated string" parse failure). Mode 2
    # emits 3 prompts/scene, so its effective batch is halved (see scene_engine).
    scene_breakdown_batch: int = 40
    scene_render_fps: int = 30
    scene_crossfade_seconds: float = 0.6       # Mode 2 crossfade between A/B/C
    max_video_seconds: int = 2400              # hard ceiling (40 min)
    # Render scene clips in parallel (FFmpeg is a subprocess → releases the GIL).
    # The #1 speed lever for long videos: turns N sequential encodes into ~N/conc.
    scene_render_concurrency: int = 4

    # ── Scene-to-scene transitions (config-driven "editing") ──────────────────
    # xfade type between consecutive scenes ("" = hard cuts). Disabled above
    # scene_transition_max_scenes to keep very long renders fast + reliable.
    scene_transition: str = "fade"             # fade | fadeblack | slideleft | ...
    scene_transition_seconds: float = 0.4
    scene_transition_max_scenes: int = 40
    # Vary the transition per scene pair by the incoming scene's emotion (see
    # schemas.common.transition_for_emotion). False = always scene_transition.
    scene_transition_variety: bool = True
    # Apply the per-style/niche grade pass (vignette/grain/eq — see
    # schemas.common.grade_filter) on the stitched timeline. Rides the xfade
    # encode, so it only applies when transitions are on.
    scene_style_grade: bool = True

    # ── Rate limiting (per user, by plan tier) ────────────────────────────────
    # Requests/min to /projects/{id}/generate, and max simultaneously-active jobs.
    rate_per_min_free: int = 5
    rate_per_min_pro: int = 30
    rate_per_min_premium: int = 60
    max_concurrent_free: int = 1
    max_concurrent_pro: int = 3
    max_concurrent_premium: int = 10

    # Watchdog: a project is "stuck" only if it has made NO progress (no new
    # job_event) for this long, at which point it is auto-retried then timed out.
    watchdog_stale_minutes: int = 35

    # ── Celery Queue Concurrency ──────────────────────────────────────────────
    celery_fast_concurrency: int = 10
    celery_media_concurrency: int = 4

    def mode_multiplier(self, render_mode: str) -> int:
        """Internal COST-metric multiplier for a render mode (mode_1 / mode_2).
        Not used for billing — billing is a per-video quota (see PLANS)."""
        if render_mode == "mode_2":
            return self.mode_2_credit_multiplier
        return self.mode_1_credit_multiplier

    def rate_per_min(self, plan_tier: str) -> int:
        if plan_tier == "free":
            return self.rate_per_min_free
        if plan_tier in _PREMIUM_PLANS:
            return self.rate_per_min_premium
        return self.rate_per_min_pro

    def max_concurrent(self, plan_tier: str) -> int:
        if plan_tier == "free":
            return self.max_concurrent_free
        if plan_tier in _PREMIUM_PLANS:
            return self.max_concurrent_premium
        return self.max_concurrent_pro

    def scene_transitions_on(self, scene_count: int) -> bool:
        """Whether scene-to-scene crossfades apply for this many scenes. Off for
        1 scene and above the guard cap (long videos keep fast hard cuts). Both
        the render and assembly stages call this so the timeline stays in sync."""
        return bool(self.scene_transition) and 1 < scene_count <= self.scene_transition_max_scenes

    # ── Plan catalog helpers ──────────────────────────────────────────────────
    def plan(self, plan_id: str) -> "Plan":
        """Return the Plan for a plan id, falling back to the free plan."""
        return PLANS.get(plan_id, PLANS["free"])

    def plan_variant_id(self, plan_id: str) -> str:
        """Lemon Squeezy variant id configured for a plan ("" if none)."""
        p = PLANS.get(plan_id)
        if not p or not p.variant_env:
            return ""
        return str(getattr(self, p.variant_env, "") or "")

    def plan_for_variant(self, variant_id: str | int) -> str:
        """Map a Lemon Squeezy variant id back to our plan id (free if unknown)."""
        vid = str(variant_id)
        if vid:
            for pid, p in PLANS.items():
                if p.variant_env and str(getattr(self, p.variant_env, "") or "") == vid:
                    return pid
        return "free"


@lru_cache
def get_settings() -> Settings:
    return Settings()
