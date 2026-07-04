from fastapi import APIRouter, HTTPException, status
from app.core.deps import CurrentUserId
from app.schemas.campaign import CampaignCreate, CampaignUpdate, CampaignResponse
from app.services.supabase import get_supabase_client
import uuid

router = APIRouter(prefix="/campaigns", tags=["campaigns"])


@router.get("", response_model=list[CampaignResponse])
async def list_campaigns(user_id: CurrentUserId):
    client = get_supabase_client()
    result = (
        client.table("campaigns")
        .select("*")
        .eq("user_id", user_id)
        .order("created_at", desc=True)
        .execute()
    )
    return result.data or []


@router.post("", status_code=status.HTTP_201_CREATED, response_model=CampaignResponse)
async def create_campaign(body: CampaignCreate, user_id: CurrentUserId):
    client = get_supabase_client()
    row = {
        "id": str(uuid.uuid4()),
        "user_id": user_id,
        "name": body.name,
        "persona_id": body.persona_id,
        "script_id": body.script_id,
        "voice_config_id": body.voice_config_id,
        "schedule_type": body.schedule_type,
        "schedule_cron": body.schedule_cron,
        "next_run_at": body.next_run_at.isoformat() if body.next_run_at else None,
        "status": "active",
    }
    result = client.table("campaigns").insert(row).execute()
    return result.data[0]


@router.put("/{campaign_id}", response_model=CampaignResponse)
async def update_campaign(campaign_id: str, body: CampaignUpdate, user_id: CurrentUserId):
    client = get_supabase_client()
    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No fields to update")

    result = (
        client.table("campaigns")
        .update(updates)
        .eq("id", campaign_id)
        .eq("user_id", user_id)
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Campaign not found")
    return result.data[0]


@router.delete("/{campaign_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_campaign(campaign_id: str, user_id: CurrentUserId):
    client = get_supabase_client()
    result = (
        client.table("campaigns")
        .delete()
        .eq("id", campaign_id)
        .eq("user_id", user_id)
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Campaign not found")
