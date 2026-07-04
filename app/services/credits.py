from app.services.supabase import get_supabase_client
from app.core.config import get_settings

settings = get_settings()


def calculate_project_credits(duration_seconds: int, render_mode: str) -> int:
    """credits = ceil(duration / 30) × mode_multiplier (AutoScene, config-driven)."""
    import math
    units = math.ceil(duration_seconds / settings.credit_seconds_per_unit)
    multiplier = settings.mode_multiplier(render_mode)
    return units * multiplier


def consume_video_quota(user_id: str, project_id: str, request_id: str) -> dict:
    """Atomically consume 1 video from the user's monthly quota and flip an
    existing draft project to 'pending'. Applies the no-rollover monthly reset
    and raises ValueError when the quota is exhausted.
    Uses the consume_video_quota_atomic stored procedure for atomicity."""
    client = get_supabase_client()
    result = client.rpc(
        "consume_video_quota_atomic",
        {
            "p_user_id": user_id,
            "p_project_id": project_id,
            "p_request_id": request_id,
        },
    ).execute()
    if not result.data:
        raise ValueError("Video quota exhausted or project render start failed")
    return result.data


def refund_credits(user_id: str, job_id: str, credits: int) -> None:
    """Refund credits when a job fails after max retries."""
    client = get_supabase_client()
    client.rpc(
        "refund_credits",
        {
            "p_user_id": user_id,
            "p_job_id": job_id,
            "p_credits": credits,
        },
    ).execute()
