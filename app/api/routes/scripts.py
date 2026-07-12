from fastapi import APIRouter, HTTPException, status
from app.core.deps import CurrentUserId
from app.schemas.script import (
    ScriptGenerateRequest,
    ScriptGenerateResponse,
    ScriptSaveRequest,
    ScriptUpdateRequest,
    ScriptResponse,
)
from app.services.supabase import get_supabase_client
from app.services.openai_service import generate_script, estimate_duration_seconds
import uuid

router = APIRouter(prefix="/scripts", tags=["scripts"])


@router.get("", response_model=list[ScriptResponse])
async def list_scripts(user_id: CurrentUserId):
    client = get_supabase_client()
    result = (
        client.table("scripts")
        .select("*")
        .eq("user_id", user_id)
        .order("created_at", desc=True)
        .execute()
    )
    return result.data or []


@router.post("/generate", response_model=ScriptGenerateResponse)
async def generate_script_endpoint(body: ScriptGenerateRequest, _user_id: CurrentUserId):
    content = await generate_script(
        title=body.title,
        product_name=body.product_name,
        target_audience=body.target_audience,
        tone=body.tone,
        goal=body.goal,
        style=body.style,
        niche=body.niche,
        target_duration_seconds=body.target_duration_seconds,
    )
    word_count = len(content.split())
    return ScriptGenerateResponse(
        content=content,
        word_count=word_count,
        estimated_duration_seconds=estimate_duration_seconds(content),
    )


@router.post("", status_code=status.HTTP_201_CREATED, response_model=ScriptResponse)
async def save_script(body: ScriptSaveRequest, user_id: CurrentUserId):
    client = get_supabase_client()
    script_id = str(uuid.uuid4())
    row = {
        "id": script_id,
        "user_id": user_id,
        "title": body.title,
        "content": body.content,
        "generation_mode": body.mode,
        "is_locked": body.mode == "custom",
        "product_name": body.product_name,
        "tone": body.tone,
        "target_audience": body.target_audience,
        "goal": body.goal,
        "style": body.style,
        "niche": body.niche,
        "duration_seconds": body.duration_seconds,
    }
    result = client.table("scripts").insert(row).execute()
    return result.data[0]


@router.put("/{script_id}", response_model=ScriptResponse)
async def update_script(script_id: str, body: ScriptUpdateRequest, user_id: CurrentUserId):
    client = get_supabase_client()

    # Verify ownership and check lock
    existing = (
        client.table("scripts")
        .select("user_id, is_locked")
        .eq("id", script_id)
        .eq("user_id", user_id)
        .single()
        .execute()
    )
    if not existing.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Script not found")
    # Custom scripts are immutable (CLAUDE.md decision #8): no AI rewriting/editing.
    if existing.data.get("is_locked"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Custom scripts are immutable and cannot be edited.",
        )

    updates = body.model_dump(exclude_none=True)
    result = client.table("scripts").update(updates).eq("id", script_id).execute()
    return result.data[0]


@router.delete("/{script_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_script(script_id: str, user_id: CurrentUserId):
    client = get_supabase_client()
    result = client.table("scripts").delete().eq("id", script_id).eq("user_id", user_id).execute()
    if not result.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Script not found")
