from datetime import datetime, timezone, timedelta
from app.core.celery_app import celery_app
from app.core.config import get_settings
from app.services.supabase import get_supabase_client


@celery_app.task(queue="fast")
def run_due_campaigns():
    """DORMANT: campaign scheduling is parked pending an AutoScene (projects) port.

    The legacy avatar runner (insert into video_jobs → run_video_pipeline) was
    removed with the HeyGen pipeline. This no-op keeps the Beat target importable
    until campaigns are rebuilt on top of projects. The campaign-runner Beat entry
    is currently disabled in celery_app.py.
    """
    return


@celery_app.task(queue="fast")
def reset_expired_video_quotas():
    """Celery Beat: refill monthly video quotas whose period has ended.

    The generate path already lazy-resets inside consume_credits_atomic, so
    this only keeps a user's DISPLAYED quota fresh when they don't generate right
    at the boundary. No rollover — balance is set to monthly_quota, not summed."""
    client = get_supabase_client()
    result = client.rpc("reset_expired_quotas", {}).execute()
    return {"reset": result.data}


# Projects that are NOT actively rendering — the watchdog skips these outright.
# `draft` and `scenes_ready` are "waiting for the user" states (not stuck renders).
# Pipeline failure states ("failed_at_*") are caught separately via the prefix check.
_INACTIVE_STATUSES = ["draft", "scenes_ready", "completed", "cancelled", "timed_out"]


@celery_app.task(queue="fast")
def watchdog_stuck_jobs():
    """Celery Beat: time out AutoScene projects that have made NO progress for too long.

    "Progress" = the latest job_event for the project (falls back to created_at if a
    project somehow has no events). Keying off last-progress (not created_at) means a
    slow-but-healthy render is never killed. Two-strike: first stall → resume-aware
    auto-retry of the pipeline; second stall → timed_out + refund + flagged for review.
    """
    client = get_supabase_client()
    stale_minutes = get_settings().watchdog_stale_minutes
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(minutes=stale_minutes)).isoformat()

    # Candidates: actively-rendering projects at least `stale_minutes` old (younger
    # ones cannot be stale yet). Staleness is then confirmed via last-progress time.
    candidates = (
        client.table("projects")
        .select("id, user_id, credits_used, created_at, status")
        .not_.in_("status", _INACTIVE_STATUSES)
        .lt("created_at", cutoff)
        .execute()
        .data
        or []
    )

    for project in candidates:
        # Skip terminal failure states (failed_at_script / _breakdown / _images / ...).
        if project["status"].startswith("failed"):
            continue

        ev = (
            client.table("job_events")
            .select("created_at")
            .eq("project_id", project["id"])
            .order("created_at", desc=True)
            .limit(1)
            .execute()
            .data
        )
        last_progress = ev[0]["created_at"] if ev else project["created_at"]
        idle_seconds = (now - datetime.fromisoformat(last_progress)).total_seconds()
        if idle_seconds < stale_minutes * 60:
            continue  # made progress recently — still healthy

        # Has the watchdog already auto-retried this project once?
        retried = (
            client.table("job_events")
            .select("id", count="exact")
            .eq("project_id", project["id"])
            .eq("stage", "watchdog_retry")
            .execute()
            .count
        ) or 0

        from app.workers.tasks.project_common import log_event

        if retried == 0:
            # First strike: resume-aware auto-retry instead of giving up.
            from app.workers.tasks.scene_breakdown import run_project_pipeline
            log_event(project["id"], "watchdog_retry", "completed",
                      metadata={"reason": f"no progress for {stale_minutes}m, auto-retrying"})
            run_project_pipeline.apply_async(args=[project["id"]], priority=0, queue="fast")
            continue

        # Second strike: give up — mark timed_out, surface for review, refund.
        log_event(project["id"], "assembly", "failed",
                  metadata={"error": "watchdog: stalled after auto-retry", "needs_review": True})
        client.table("projects").update({
            "status": "timed_out",
            "error_message": "This video took too long and timed out. Your credits have been refunded — please try again.",
        }).eq("id", project["id"]).execute()

        if project.get("credits_used", 0) > 0:
            from app.services.credits import refund_credits
            refund_credits(project["user_id"], project["id"])
