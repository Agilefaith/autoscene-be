from fastapi import APIRouter, HTTPException, status

from app.core.deps import CurrentUserId, CurrentUser
from app.schemas.scene import SceneResponse, SceneUpdate
from app.services.supabase import get_supabase_client

router = APIRouter(prefix="/scenes", tags=["scenes"])

_BUSY_PROJECT = {
    "generating_images", "rendering_scenes", "voiceover", "assembling", "pending",
}


def _owned_scene(scene_id: str, user_id: str) -> dict:
    row = (
        get_supabase_client().table("scenes").select("*")
        .eq("id", scene_id).eq("user_id", user_id).single().execute().data
    )
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Scene not found")
    return row


@router.get("")
async def list_scenes(user_id: CurrentUserId, limit: int = 100, offset: int = 0):
    """List all of the user's scenes across projects (Scenes library). Each scene
    embeds its parent project's name + status."""
    return (
        get_supabase_client().table("scenes")
        .select("*, projects(name, status)")
        .eq("user_id", user_id)
        .order("created_at", desc=True)
        .range(offset, offset + limit - 1)
        .execute().data or []
    )


@router.patch("/{scene_id}", response_model=SceneResponse)
async def update_scene(scene_id: str, body: SceneUpdate, user_id: CurrentUserId):
    """Edit a scene's text/prompt/motion before the render runs (PRD §9 scene
    preview editing)."""
    _owned_scene(scene_id, user_id)
    updates = body.model_dump(exclude_none=True)
    if not updates:
        return _owned_scene(scene_id, user_id)
    return (
        get_supabase_client().table("scenes").update(updates)
        .eq("id", scene_id).eq("user_id", user_id).execute().data[0]
    )


@router.post("/{scene_id}/regenerate-image", status_code=status.HTTP_202_ACCEPTED)
async def regenerate_scene_image(scene_id: str, user_id: CurrentUserId, user: CurrentUser):
    """Re-run SDXL for a single scene (e.g. after editing its prompt). Rate-limited
    per plan so unlimited preview regenerations can't quietly run up SDXL cost."""
    scene = _owned_scene(scene_id, user_id)
    client = get_supabase_client()

    # Rate limit by plan tier (skip internal) — each regen is a paid SDXL call.
    if user.get("user_type") != "internal":
        from app.services.ratelimit import check_request_rate
        if not check_request_rate(user_id, user.get("plan_tier", "free")):
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="You're regenerating images too quickly. Please wait a moment and try again.",
            )

    project = (
        client.table("projects").select("status")
        .eq("id", scene["project_id"]).single().execute().data
    )
    if project and project["status"] in _BUSY_PROJECT:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Project is busy.")

    client.table("scenes").update({"status": "pending", "error_message": None}).eq("id", scene_id).execute()
    from app.workers.tasks.image_gen import regenerate_single_scene
    regenerate_single_scene.apply_async(args=[scene_id], queue="media")
    return {"scene_id": scene_id, "status": "pending"}
