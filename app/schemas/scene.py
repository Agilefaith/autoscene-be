from pydantic import BaseModel
from datetime import datetime
from typing import Optional, Any


class SceneResponse(BaseModel):
    id: str
    project_id: str
    idx: int
    scene_text: Optional[str] = None
    emotion: Optional[str] = None
    action: Optional[str] = None
    environment: Optional[str] = None
    image_prompt: Optional[str] = None      # the AI's suggestion from the breakdown
    user_prompt: Optional[str] = None       # what the user wrote; authoritative when set
    image_prompts: Optional[list[str]] = None
    seed: Optional[int] = None
    motion_type: Optional[str] = None
    image_urls: Optional[list[str]] = None
    clip_url: Optional[str] = None
    duration_seconds: float
    status: str
    error_message: Optional[str] = None
    created_at: datetime


class SceneUpdate(BaseModel):
    """User edits to a scene before/after generation (PRD §9 scene preview editing)."""
    scene_text: Optional[str] = None
    image_prompt: Optional[str] = None
    user_prompt: Optional[str] = None
    image_prompts: Optional[list[str]] = None
    motion_type: Optional[str] = None


class ScenePromptEntry(BaseModel):
    """One scene's manual image prompt, addressed by its position in the project."""
    idx: int
    user_prompt: str


class ScenePromptsUpdate(BaseModel):
    """Save the manual image prompts for a whole project in one call.

    The create flow writes every scene at once (including a bulk paste of all
    prompts), so saving them one request per scene would mean 100+ round trips
    on a long video.
    """
    prompts: list[ScenePromptEntry]
