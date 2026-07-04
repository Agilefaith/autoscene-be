from pydantic import BaseModel
from datetime import datetime
from typing import Optional, Literal


class PlatformVoice(BaseModel):
    id: str
    name: str
    accent: str
    gender: Literal["male", "female", "non-binary"]
    provider: Literal["elevenlabs", "minimax"] = "elevenlabs"
    preview_url: Optional[str] = None


class VoiceValidateRequest(BaseModel):
    voice_id: str
    provider: Literal["elevenlabs", "minimax"] = "elevenlabs"
    nickname: str = ""


class VoiceSaveRequest(BaseModel):
    name: str
    provider: Literal["elevenlabs", "minimax"]
    voice_id: str
    is_custom: bool = True
    validated: bool = False


class VoiceResponse(BaseModel):
    id: str
    user_id: str
    name: str
    provider: str
    voice_id: str
    is_custom: bool
    validated: bool
    created_at: datetime


class VoiceValidateResponse(BaseModel):
    valid: bool
    voice_id: str
    name: Optional[str] = None
    provider: str
