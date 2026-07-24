from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional

from app.schemas.common import RenderMode, AutoSceneFormat, SubtitleSettings
from app.schemas.scene import SceneResponse


class CharacterRef(BaseModel):
    """One named cast member: a reference image plus the name the script uses for
    them. `description` is the locked physical description built from the image by
    app/services/character_sheet.py — the client never sets it.

    An empty name is allowed: legacy single-reference projects have no name, and a
    nameless reference still conditions the image engine. It just can't take part
    in the by-name identity lock (see character_sheet.cast_block)."""
    name: str = Field("", max_length=60)
    image_url: str
    description: Optional[str] = None


class ProjectCreate(BaseModel):
    """Create a new project (draft). Only a name is strictly required; the rest is
    filled in across the Script → Configure steps."""
    name: str = "Untitled Project"
    script_id: Optional[str] = None
    voice_config_id: Optional[str] = None
    reference_image_url: Optional[str] = None
    characters: list[CharacterRef] = []
    render_mode: RenderMode = "mode_1"
    format: AutoSceneFormat = "9:16"
    niche: Optional[str] = None
    style: Optional[str] = None
    duration_seconds: int = 60
    subtitle_settings: SubtitleSettings = SubtitleSettings()


class ProjectUpdate(BaseModel):
    """Patch project configuration on the Configure step. All fields optional."""
    name: Optional[str] = None
    script_id: Optional[str] = None
    voice_config_id: Optional[str] = None
    reference_image_url: Optional[str] = None
    characters: Optional[list[CharacterRef]] = None
    render_mode: Optional[RenderMode] = None
    format: Optional[AutoSceneFormat] = None
    niche: Optional[str] = None
    style: Optional[str] = None
    duration_seconds: Optional[int] = None
    subtitle_settings: Optional[SubtitleSettings] = None


class ProjectGenerateRequest(BaseModel):
    """Kick off the full render. request_id is the client-generated idempotency key."""
    request_id: str = Field(..., description="Client-generated UUID for idempotency")


class ProjectResponse(BaseModel):
    id: str
    user_id: str
    name: str
    script_id: Optional[str] = None
    voice_config_id: Optional[str] = None
    reference_image_url: Optional[str] = None
    characters: list[CharacterRef] = []
    request_id: Optional[str] = None
    render_mode: str
    format: str
    niche: Optional[str] = None
    style: Optional[str] = None
    duration_seconds: int
    scene_duration_seconds: int
    subtitle_enabled: bool
    subtitle_font: Optional[str] = None
    subtitle_size: Optional[int] = None
    subtitle_color: Optional[str] = None
    subtitle_position: Optional[str] = None
    status: str
    credits_used: int
    voiceover_url: Optional[str] = None
    final_video_url: Optional[str] = None
    thumbnail_urls: Optional[list[str]] = None
    error_message: Optional[str] = None
    processing_time_ms: Optional[int] = None
    created_at: datetime
    completed_at: Optional[datetime] = None


class ProjectDetailResponse(ProjectResponse):
    scenes: list[SceneResponse] = []
