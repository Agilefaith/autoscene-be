from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache
from dataclasses import dataclass


@dataclass(frozen=True)
class Plan:
    """A subscription plan (config-driven).

    Billing is in CREDITS, where one credit is one minute of finished video
    (Faith, 2026-08-05). Plan credits are a monthly allowance with no rollover;
    separately-purchased top-up credits do not expire (see TOPUP_PACKS and the
    credits table's topup_balance). Prices are in Naira because the Paystack
    account settles in NGN and cannot charge USD.
    """
    id: str
    name: str
    price_ngn: int              # monthly price in whole Naira
    credits_per_month: int      # 1 credit = 1 minute of video
    queue_priority: int         # Celery priority; lower drains first
    plan_code_env: str          # Settings attr holding the Paystack plan code


@dataclass(frozen=True)
class TopupPack:
    """A one-off Pay-As-You-Go credit purchase. Not a subscription: it is a single
    charge that adds credits which never expire."""
    id: str
    credits: int
    price_ngn: int


# The AutoScene plan catalog (config-driven). The free tier and both Mode 2 plans
# were removed on Faith's instruction (2026-08-05); Mode 1 is the only render mode.
PLANS: dict[str, "Plan"] = {
    "starter": Plan("starter", "Starter",  22_400,  20, 4, "paystack_starter_plan_code"),
    "creator": Plan("creator", "Creator",  57_400,  60, 2, "paystack_creator_plan_code"),
    "pro":     Plan("pro",     "Pro",     129_400, 150, 0, "paystack_pro_plan_code"),
    "scale":   Plan("scale",   "Scale",   260_400, 350, 0, "paystack_scale_plan_code"),
}

TOPUP_PACKS: dict[str, "TopupPack"] = {
    "topup_80":  TopupPack("topup_80",   80,  96_000),
    "topup_180": TopupPack("topup_180", 180, 176_000),
}

# Plans that get premium-level rate limits / concurrency.
_PREMIUM_PLANS = {"pro", "scale"}


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

    # ── Access control ────────────────────────────────────────────────────────
    # The app is invite-only. This address is the administrator: the only account
    # that can invite others (see api/routes/admin.py). Config-driven so it can be
    # changed without a migration.
    admin_email: str = "faithfuliselen@gmail.com"

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

    # ── Paystack (replaced Lemon Squeezy, 2026-08-05) ─────────────────────────
    # The account settles in NGN and cannot charge USD, so all pricing is Naira.
    paystack_secret_key: str = ""
    paystack_public_key: str = ""
    # Paystack signs webhooks with the SECRET key (HMAC-SHA512), so there is no
    # separate webhook secret to configure.
    # Subscription plan codes, created once in Paystack (scripts/paystack_setup.py).
    paystack_starter_plan_code: str = ""
    paystack_creator_plan_code: str = ""
    paystack_pro_plan_code: str = ""
    paystack_scale_plan_code: str = ""
    # Where Paystack sends the customer back after checkout.
    paystack_callback_url: str = ""

    # ── Billing config (config-driven) ────────────────────────────────────────
    # One credit = one minute of finished video, rounded up: a 90s video costs 2.
    seconds_per_credit: int = 60

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
    # 40 min at 10s = 240 scenes; stanza-cut niches produce far more (a 40-min
    # script can run ~600 two-line stanzas), and hitting the cap silently
    # stretched scenes well past their target length.
    scene_max_count: int = 600
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

    def credits_for(self, duration_seconds: int) -> int:
        """Credits a video costs: one per started minute (90s costs 2)."""
        import math
        return max(1, math.ceil(max(1, duration_seconds) / self.seconds_per_credit))

    def rate_per_min(self, plan_tier: str) -> int:
        if plan_tier in _PREMIUM_PLANS:
            return self.rate_per_min_premium
        return self.rate_per_min_pro

    def max_concurrent(self, plan_tier: str) -> int:
        if plan_tier in _PREMIUM_PLANS:
            return self.max_concurrent_premium
        return self.max_concurrent_pro

    def scene_transitions_on(self, scene_count: int) -> bool:
        """Whether scene-to-scene crossfades apply for this many scenes. Off for
        1 scene and above the guard cap (long videos keep fast hard cuts). Both
        the render and assembly stages call this so the timeline stays in sync."""
        return bool(self.scene_transition) and 1 < scene_count <= self.scene_transition_max_scenes

    # ── Plan catalog helpers ──────────────────────────────────────────────────
    def plan(self, plan_id: str) -> "Plan | None":
        """The Plan for a plan id, or None. There is no free tier to fall back to
        any more, so callers must handle an unsubscribed user explicitly."""
        return PLANS.get(plan_id)

    def plan_code(self, plan_id: str) -> str:
        """Paystack plan code configured for a plan ("" if not yet created)."""
        p = PLANS.get(plan_id)
        return str(getattr(self, p.plan_code_env, "") or "") if p else ""

    def plan_for_code(self, plan_code: str) -> str | None:
        """Map a Paystack plan code back to our plan id."""
        code = str(plan_code or "")
        if code:
            for pid, p in PLANS.items():
                if str(getattr(self, p.plan_code_env, "") or "") == code:
                    return pid
        return None


@lru_cache
def get_settings() -> Settings:
    return Settings()
