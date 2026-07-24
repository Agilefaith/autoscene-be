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
    # User's own provider API key — required for private voices, since a voice ID
    # is only reachable with a key from the account that owns (or was shared) it.
    api_key: Optional[str] = None


class VoiceSaveRequest(BaseModel):
    name: str
    provider: Literal["elevenlabs", "minimax"]
    voice_id: str
    is_custom: bool = True
    validated: bool = False
    api_key: Optional[str] = None  # stored encrypted; never returned


class VoiceResponse(BaseModel):
    """NOTE: deliberately has NO api_key field — stored keys are never returned.
    `has_api_key` only signals that a key is on file."""
    id: str
    user_id: str
    name: str
    provider: str
    voice_id: str
    is_custom: bool
    validated: bool
    created_at: datetime
    has_api_key: bool = False


class VoiceValidateResponse(BaseModel):
    valid: bool
    voice_id: str
    name: Optional[str] = None
    provider: str
    # Why validation failed: "not_accessible" (needs the user's own key),
    # "invalid_key" (their key was rejected), or "invalid".
    reason: Optional[str] = None
