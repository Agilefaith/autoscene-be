from fastapi import APIRouter, HTTPException, status
from fastapi.responses import Response
from app.core.deps import CurrentUserId
from app.schemas.voice import (
    VoiceValidateRequest,
    VoiceValidateResponse,
    VoiceSaveRequest,
    VoiceResponse,
)
from app.services.supabase import get_supabase_client
from app.services.elevenlabs import (
    get_preset_voices,
    validate_voice_id as el_validate,
    generate_preview_audio as el_preview,
)
from app.services.minimax import (
    validate_voice_id as mm_validate,
    generate_preview_audio as mm_preview,
)
from app.services import crypto
import uuid

router = APIRouter(prefix="/voices", tags=["voices"])

_preview_cache: dict[str, bytes] = {}


@router.get("/preset", response_model=list[dict])
async def list_preset_voices():
    return await get_preset_voices()


@router.get("/preview/{elevenlabs_voice_id}")
async def preview_voice(elevenlabs_voice_id: str):
    """ElevenLabs TTS preview (used by platform voices and ElevenLabs custom voices)."""
    if elevenlabs_voice_id in _preview_cache:
        return Response(content=_preview_cache[elevenlabs_voice_id], media_type="audio/mpeg")

    audio = await el_preview(elevenlabs_voice_id)
    if audio is None:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Failed to generate preview")

    _preview_cache[elevenlabs_voice_id] = audio
    return Response(content=audio, media_type="audio/mpeg")


@router.get("/preview/minimax/{voice_id}")
async def preview_minimax_voice(voice_id: str):
    """Minimax TTS preview for custom Minimax voices."""
    cache_key = f"mm:{voice_id}"
    if cache_key in _preview_cache:
        return Response(content=_preview_cache[cache_key], media_type="audio/mpeg")

    audio = await mm_preview(voice_id)
    if audio is None:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Failed to generate preview")

    _preview_cache[cache_key] = audio
    return Response(content=audio, media_type="audio/mpeg")


@router.get("/preview/config/{config_id}")
async def preview_saved_voice(config_id: str, user_id: CurrentUserId):
    """Preview a SAVED voice config, using its stored per-user API key when one
    is on file (private voices aren't reachable with the platform key)."""
    client = get_supabase_client()
    row = (
        client.table("voice_configs").select("provider, voice_id, api_key_encrypted")
        .eq("id", config_id).eq("user_id", user_id).single().execute().data
    )
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Voice not found")

    api_key = crypto.decrypt(row.get("api_key_encrypted"))
    cache_key = f"cfg:{config_id}"
    if cache_key in _preview_cache:
        return Response(content=_preview_cache[cache_key], media_type="audio/mpeg")

    if row["provider"] == "minimax":
        audio = await mm_preview(row["voice_id"], api_key=api_key)
    else:
        audio = await el_preview(row["voice_id"], api_key=api_key)
    if audio is None:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Failed to generate preview")

    _preview_cache[cache_key] = audio
    return Response(content=audio, media_type="audio/mpeg")


@router.post("/validate", response_model=VoiceValidateResponse)
async def validate_custom_voice(body: VoiceValidateRequest, _: CurrentUserId):
    """Validate a voice ID, optionally with the user's own provider API key.
    Private voices are only reachable with their owner's key, so `reason`
    tells the UI whether to ask the user for one."""
    api_key = (body.api_key or "").strip() or None
    if body.provider == "elevenlabs":
        result = await el_validate(body.voice_id, api_key=api_key)
        return VoiceValidateResponse(
            valid=result["valid"],
            voice_id=body.voice_id,
            name=result.get("name"),
            provider="elevenlabs",
            reason=result.get("reason"),
        )

    result = await mm_validate(body.voice_id, api_key=api_key)
    return VoiceValidateResponse(
        valid=result["valid"],
        voice_id=body.voice_id,
        name=None,
        provider="minimax",
        reason=result.get("reason"),
    )


def _to_response(row: dict) -> dict:
    """Strip the encrypted key from an API response, exposing only its presence."""
    row = dict(row)
    row["has_api_key"] = bool(row.pop("api_key_encrypted", None))
    return row


@router.get("", response_model=list[VoiceResponse])
async def list_saved_voices(user_id: CurrentUserId):
    """List the user's OWN voices (custom voices they added by voice ID).

    Platform/preset voices selected during video creation are persisted as
    voice_configs too (the video FK needs one), but with is_custom=false — those
    are NOT "your voices", so they're excluded here.
    """
    client = get_supabase_client()
    result = (
        client.table("voice_configs")
        .select("*")
        .eq("user_id", user_id)
        .eq("is_custom", True)
        .order("created_at", desc=True)
        .execute()
    )
    return [_to_response(r) for r in (result.data or [])]


@router.post("", status_code=status.HTTP_201_CREATED, response_model=VoiceResponse)
async def save_voice(body: VoiceSaveRequest, user_id: CurrentUserId):
    client = get_supabase_client()
    api_key = (body.api_key or "").strip() or None

    # Idempotent: reuse an existing config for the same provider+voice_id so
    # re-selecting a preset (or re-saving a custom voice) never creates duplicates.
    existing = (
        client.table("voice_configs")
        .select("*")
        .eq("user_id", user_id)
        .eq("provider", body.provider)
        .eq("voice_id", body.voice_id)
        .limit(1)
        .execute()
    )
    if existing.data:
        row = existing.data[0]
        # Re-saving with a key attaches/refreshes it on the existing config.
        if api_key:
            row = (
                client.table("voice_configs")
                .update({"api_key_encrypted": crypto.encrypt(api_key), "validated": body.validated})
                .eq("id", row["id"]).execute().data[0]
            )
        return _to_response(row)

    row = {
        "id": str(uuid.uuid4()),
        "user_id": user_id,
        "name": body.name,
        "provider": body.provider,
        "voice_id": body.voice_id,
        "is_custom": body.is_custom,
        "validated": body.validated,
        "api_key_encrypted": crypto.encrypt(api_key) if api_key else None,
    }
    result = client.table("voice_configs").insert(row).execute()
    return _to_response(result.data[0])


@router.delete("/{voice_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_voice(voice_id: str, user_id: CurrentUserId):
    client = get_supabase_client()
    result = (
        client.table("voice_configs")
        .delete()
        .eq("id", voice_id)
        .eq("user_id", user_id)
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Voice not found")
