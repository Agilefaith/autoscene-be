from pydantic import BaseModel, field_validator
from datetime import datetime
from typing import Optional, Literal


class ScriptGenerateRequest(BaseModel):
    title: str
    product_name: str
    target_audience: str
    tone: str
    goal: Literal["sales", "engagement", "education", "storytelling"]
    style: Literal["ugc", "ad", "tiktok_hook", "documentary", "ai_influencer"]
    # Content niche (e.g. "Bible storytelling") — shapes the script's subject and voice.
    niche: str = ""
    # Drives script length so AI scripts fit the intended video duration.
    target_duration_seconds: int = 30


class ScriptSaveRequest(BaseModel):
    title: str
    content: str
    mode: Literal["ai", "custom"]
    product_name: Optional[str] = None
    tone: Optional[str] = None
    target_audience: Optional[str] = None
    goal: Optional[str] = None
    style: Optional[str] = None


class ScriptUpdateRequest(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None


class ScriptResponse(BaseModel):
    id: str
    user_id: str
    title: str
    content: str
    generation_mode: str
    is_locked: bool
    product_name: Optional[str] = None
    tone: Optional[str] = None
    target_audience: Optional[str] = None
    goal: Optional[str] = None
    style: Optional[str] = None
    created_at: datetime


class ScriptGenerateResponse(BaseModel):
    content: str
    estimated_duration_seconds: int
    word_count: int
