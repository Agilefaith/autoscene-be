"""Shared helpers for the AutoScene project pipeline (mirrors script_gen helpers
for the legacy avatar pipeline, but keyed on project_id via job_events.project_id)."""

from app.services.supabase import get_supabase_client


def log_event(project_id: str, stage: str, event_status: str,
              duration_ms: int = 0, metadata: dict | None = None) -> None:
    get_supabase_client().table("job_events").insert({
        "project_id": project_id,
        "stage": stage,
        "status": event_status,
        "duration_ms": duration_ms,
        "metadata": metadata or {},
    }).execute()


def update_project(project_id: str, updates: dict) -> None:
    get_supabase_client().table("projects").update(updates).eq("id", project_id).execute()


def get_project(project_id: str) -> dict | None:
    return (
        get_supabase_client().table("projects").select("*")
        .eq("id", project_id).single().execute().data
    )


def get_scenes(project_id: str) -> list[dict]:
    return (
        get_supabase_client().table("scenes").select("*")
        .eq("project_id", project_id).order("idx").execute().data or []
    )


def is_cancelled(project_id: str) -> bool:
    """True if the user cancelled the project. Checked at each stage boundary so a
    cancel actually stops further work (and further spend)."""
    row = (
        get_supabase_client().table("projects").select("status")
        .eq("id", project_id).single().execute().data
    )
    return bool(row) and row.get("status") == "cancelled"


# User-facing failure messages per pipeline stage (raw error kept in job_events).
_STAGE_MESSAGES = {
    "breakdown":  "We couldn't break your script into scenes. Please try again.",
    "images":     "We couldn't generate the scene images. Please try again.",
    "voiceover":  "We couldn't generate the voiceover. Please try again.",
    "render":     "We couldn't render the scene motion. Please try again.",
    "assembly":   "We couldn't finish assembling your video. Please try again.",
}


def friendly_error(stage: str) -> str:
    return _STAGE_MESSAGES.get(
        stage, "Something went wrong while creating your video. Please try again."
    )


def refund_on_final_failure(project: dict) -> None:
    """Refund a project's credits (call only on terminal failure)."""
    if project.get("credits_used", 0) > 0:
        from app.services.credits import refund_credits
        refund_credits(project["user_id"], project["id"])
