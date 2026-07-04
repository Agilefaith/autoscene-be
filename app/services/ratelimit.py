"""Per-user rate limiting backed by Redis.

Two gates, both scaled by plan tier:
- requests/min to the generate endpoint (fixed-window counter in Redis)
- max simultaneously-active jobs (counted from the DB, the source of truth)

Fails OPEN: if Redis is unavailable the request is allowed (availability over
strictness), so a Redis blip never blocks paying users.
"""
import time
import redis
from app.core.config import get_settings

settings = get_settings()

try:
    _redis = redis.from_url(settings.redis_url)
except Exception:  # pragma: no cover
    _redis = None


def check_request_rate(user_id: str, plan_tier: str) -> bool:
    """True if the user is within their requests/min budget. Fails open."""
    if _redis is None:
        return True
    limit = settings.rate_per_min(plan_tier)
    window = int(time.time() // 60)
    key = f"rl:req:{user_id}:{window}"
    try:
        count = _redis.incr(key)
        if count == 1:
            _redis.expire(key, 70)
        return count <= limit
    except Exception:
        return True  # Redis down → don't block


# Project statuses that count as "in-flight" (mirrors ACTIVE_STATUSES in
# api/routes/projects.py). Excludes draft (not rendering yet) and terminal states.
_ACTIVE_PROJECT_STATUSES = [
    "pending", "scripting", "scene_breakdown", "scenes_ready",
    "generating_images", "rendering_scenes", "voiceover", "assembling",
]


def active_job_count(user_id: str) -> int:
    """Number of the user's currently in-flight (actively rendering) projects."""
    from app.services.supabase import get_supabase_client
    res = (
        get_supabase_client()
        .table("projects")
        .select("id", count="exact")
        .eq("user_id", user_id)
        .in_("status", _ACTIVE_PROJECT_STATUSES)
        .execute()
    )
    return res.count or 0
