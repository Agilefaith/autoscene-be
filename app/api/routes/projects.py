import uuid

from fastapi import APIRouter, HTTPException, status

from app.core.deps import CurrentUserId, CurrentUser, AppSettings
from app.schemas.common import NICHES
from app.schemas.project import (
    ProjectCreate, ProjectUpdate, ProjectGenerateRequest,
    ProjectResponse, ProjectDetailResponse,
)
from app.services.supabase import get_supabase_client
from app.services.credits import consume_video_quota
from app.services.openai_service import estimate_duration_seconds

router = APIRouter(prefix="/projects", tags=["projects"])

# Celery+Redis priority: 0 = highest (drained first). Three paid tiers mirror the
# pricing sheet's queue promise — Scale = priority queue, Creator = faster queue,
# Starter = standard queue — with free/trial always drained last.
PRIORITY_PAID = 0   # top tier (Scale) + internal
PRIORITY_FREE = 9
PLAN_PRIORITY = {
    "scale": 0, "scale_m2": 0,      # Priority queue
    "creator": 2, "creator_m2": 2,  # Faster queue
    "starter": 4,                   # Standard queue
    "free": PRIORITY_FREE,
}

# Statuses where the pipeline is actively running (not re-dispatchable / not editable).
# NOTE: `scenes_ready` is intentionally EXCLUDED — it's a "waiting for the user" state
# (breakdown done, ready to Configure + Generate), so PATCH, re-breakdown, and generate
# must all be allowed from it. `draft` is likewise editable/dispatchable.
ACTIVE_STATUSES = {
    "pending", "scripting", "scene_breakdown",
    "generating_images", "rendering_scenes", "voiceover", "assembling",
}
TERMINAL_STATUSES = {"completed", "cancelled"}


def _owned_project(project_id: str, user_id: str, columns: str = "*") -> dict:
    client = get_supabase_client()
    row = (
        client.table("projects").select(columns)
        .eq("id", project_id).eq("user_id", user_id).single().execute().data
    )
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return row


@router.get("/options/niches", response_model=list[str])
async def list_niches(_user_id: CurrentUserId):
    return NICHES


@router.post("", status_code=status.HTTP_201_CREATED, response_model=ProjectResponse)
async def create_project(body: ProjectCreate, user_id: CurrentUserId):
    client = get_supabase_client()
    project_id = str(uuid.uuid4())
    row = {
        "id": project_id,
        "user_id": user_id,
        "name": body.name,
        "script_id": body.script_id,
        "voice_config_id": body.voice_config_id,
        "reference_image_url": body.reference_image_url,
        "characters": [c.model_dump() for c in body.characters],
        "render_mode": body.render_mode,
        "format": body.format,
        "niche": body.niche,
        "style": body.style,
        "duration_seconds": body.duration_seconds,
        "subtitle_enabled": body.subtitle_settings.enabled,
        "subtitle_font": body.subtitle_settings.font_style,
        "subtitle_size": body.subtitle_settings.font_size,
        "subtitle_color": body.subtitle_settings.font_color,
        "subtitle_position": body.subtitle_settings.placement,
        "status": "draft",
    }
    return client.table("projects").insert(row).execute().data[0]


@router.get("", response_model=list[ProjectResponse])
async def list_projects(user_id: CurrentUserId, limit: int = 20, offset: int = 0):
    client = get_supabase_client()
    return (
        client.table("projects").select("*")
        .eq("user_id", user_id)
        .order("created_at", desc=True)
        .range(offset, offset + limit - 1)
        .execute().data or []
    )


@router.get("/{project_id}", response_model=ProjectDetailResponse)
async def get_project(project_id: str, user_id: CurrentUserId):
    project = _owned_project(project_id, user_id)
    scenes = (
        get_supabase_client().table("scenes").select("*")
        .eq("project_id", project_id).order("idx").execute().data or []
    )
    return {**project, "scenes": scenes}


@router.patch("/{project_id}", response_model=ProjectResponse)
async def update_project(project_id: str, body: ProjectUpdate, user_id: CurrentUserId):
    project = _owned_project(project_id, user_id, "id, status")
    if project["status"] in ACTIVE_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot edit a project while it is rendering.",
        )

    updates: dict = body.model_dump(exclude_none=True)
    # Flatten subtitle_settings into the project's columns.
    subs = updates.pop("subtitle_settings", None)
    if subs:
        updates.update({
            "subtitle_enabled": subs["enabled"],
            "subtitle_font": subs["font_style"],
            "subtitle_size": subs["font_size"],
            "subtitle_color": subs["font_color"],
            "subtitle_position": subs["placement"],
        })
    if not updates:
        return _owned_project(project_id, user_id)
    return (
        get_supabase_client().table("projects").update(updates)
        .eq("id", project_id).eq("user_id", user_id).execute().data[0]
    )


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(project_id: str, user_id: CurrentUserId):
    result = (
        get_supabase_client().table("projects").delete()
        .eq("id", project_id).eq("user_id", user_id).execute()
    )
    if not result.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")


@router.post("/{project_id}/breakdown", status_code=status.HTTP_202_ACCEPTED)
async def run_breakdown(project_id: str, user_id: CurrentUserId):
    """Split the project's script into scenes (Script → Scenes step). Free — no
    credits. Scenes stream back via Realtime / GET /projects/{id}."""
    project = _owned_project(project_id, user_id, "id, status, script_id")
    if not project.get("script_id"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Attach a script to the project before generating scenes.",
        )
    if project["status"] in ACTIVE_STATUSES:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Project is busy.")

    get_supabase_client().table("projects").update(
        {"status": "scene_breakdown"}
    ).eq("id", project_id).execute()

    from app.workers.tasks.scene_breakdown import run_scene_breakdown
    run_scene_breakdown.apply_async(args=[project_id], queue="fast")
    return {"project_id": project_id, "status": "scene_breakdown"}


@router.post("/{project_id}/generate", status_code=status.HTTP_202_ACCEPTED)
async def generate_project(
    project_id: str,
    body: ProjectGenerateRequest,
    user_id: CurrentUserId,
    user: CurrentUser,
    settings: AppSettings,
):
    """Kick off the full render (Generate step): credits + idempotency + dispatch."""
    client = get_supabase_client()
    project = _owned_project(project_id, user_id)

    # Idempotency: same request_id on a project that's already running/done → no-op.
    if project.get("request_id") == body.request_id and project["status"] not in ("draft", "failed_at_script", "failed_at_breakdown", "failed_at_images", "failed_at_render", "failed_at_voiceover", "failed_at_assembly"):
        return {"project_id": project_id, "status": project["status"], "duplicate": True}

    if project["status"] in ACTIVE_STATUSES:
        return {"project_id": project_id, "status": project["status"], "duplicate": True}

    if not project.get("script_id"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Project has no script.")
    if not project.get("voice_config_id"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Project has no voice selected.")

    # Ensure scenes exist (breakdown must have run).
    scene_count = (
        client.table("scenes").select("id", count="exact")
        .eq("project_id", project_id).execute().count or 0
    )
    if scene_count == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Generate scenes first (run the scene breakdown).",
        )

    plan_tier = user.get("plan_tier", "free")
    is_internal = user.get("user_type") == "internal"

    # Rate limit by tier (skip internal).
    if not is_internal:
        from app.services.ratelimit import check_request_rate, active_job_count
        if not check_request_rate(user_id, plan_tier):
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="You're creating videos too quickly. Please wait a moment and try again.",
            )
        if active_job_count(user_id) >= settings.max_concurrent(plan_tier):
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="You already have the maximum number of videos processing for your plan.",
            )

    # Duration follows the actual script (keeps quota honest), like videos.py.
    script_row = (
        client.table("scripts").select("content")
        .eq("id", project["script_id"]).eq("user_id", user_id).single().execute().data
    )
    script_content = (script_row or {}).get("content")
    duration_seconds = (
        estimate_duration_seconds(script_content) if script_content
        else project["duration_seconds"]
    )

    # Plan gating (skip internal): max duration per video + allowed render modes.
    plan = settings.plan(plan_tier)
    render_mode = project["render_mode"]
    if not is_internal:
        cap = min(plan.max_duration_seconds, settings.max_video_seconds)
        if duration_seconds > cap:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"This video is {duration_seconds // 60} min, but your {plan.name} plan "
                    f"allows up to {cap // 60} min per video. Shorten the script or upgrade."
                ),
            )
        if render_mode not in plan.allowed_modes:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Your {plan.name} plan does not include {render_mode.replace('_', ' ').title()}. Upgrade to a Mode 2 plan.",
            )

    # Persist the resolved duration, then atomically consume 1 video + flip to pending.
    client.table("projects").update({"duration_seconds": duration_seconds}).eq("id", project_id).execute()
    if is_internal:
        client.table("projects").update({
            "status": "pending", "credits_used": 0, "request_id": body.request_id, "error_message": None,
        }).eq("id", project_id).execute()
    else:
        try:
            consume_video_quota(user_id, project_id, body.request_id)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail="You've used all the videos in your plan for this period. It resets next month, or you can upgrade.",
            )

    priority = PRIORITY_PAID if is_internal else PLAN_PRIORITY.get(plan_tier, PRIORITY_FREE)
    from app.workers.tasks.scene_breakdown import run_project_pipeline
    run_project_pipeline.apply_async(args=[project_id], priority=priority, queue="fast")

    return {
        "project_id": project_id,
        "status": "pending",
        "credits_used": 0 if is_internal else 1,
    }


@router.post("/{project_id}/cancel")
async def cancel_project(project_id: str, user_id: CurrentUserId):
    """Cancel an in-flight render: mark cancelled (pipeline stops at the next stage
    boundary) and refund credits if no expensive work has billed yet."""
    client = get_supabase_client()
    project = _owned_project(project_id, user_id, "id, status, credits_used")
    if project["status"] in TERMINAL_STATUSES or project["status"].startswith("failed"):
        return {"status": project["status"], "already_terminal": True}

    client.table("projects").update({
        "status": "cancelled", "error_message": "Cancelled by user.",
    }).eq("id", project_id).execute()

    refunded = False
    if project.get("credits_used", 0) > 0:
        from app.services.credits import refund_credits
        refund_credits(user_id, project_id, project["credits_used"])
        refunded = True
    return {"status": "cancelled", "refunded": refunded}
