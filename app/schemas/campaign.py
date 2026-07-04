from pydantic import BaseModel
from datetime import datetime
from typing import Optional, Literal


class CampaignCreate(BaseModel):
    name: str
    persona_id: str
    script_id: str
    voice_config_id: str
    schedule_type: Literal["daily", "weekly", "custom"]
    schedule_cron: str  # cron expression
    next_run_at: Optional[datetime] = None


class CampaignUpdate(BaseModel):
    name: Optional[str] = None
    persona_id: Optional[str] = None
    script_id: Optional[str] = None
    voice_config_id: Optional[str] = None
    schedule_type: Optional[Literal["daily", "weekly", "custom"]] = None
    schedule_cron: Optional[str] = None
    status: Optional[Literal["active", "paused"]] = None


class CampaignResponse(BaseModel):
    id: str
    user_id: str
    name: str
    persona_id: str
    script_id: str
    voice_config_id: str
    schedule_type: str
    schedule_cron: str
    next_run_at: Optional[datetime] = None
    status: str
    created_at: datetime
