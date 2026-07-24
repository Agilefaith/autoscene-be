import httpx
from app.core.config import get_settings

settings = get_settings()
EL_BASE = "https://api.elevenlabs.io/v1"


def _headers(api_key: str | None = None) -> dict:
    """Auth headers. `api_key` is a user-supplied key (per-user custom voices);
    falls back to the platform key."""
    return {"xi-api-key": api_key or settings.elevenlabs_api_key}


PRESET_VOICES = [
    {"id": "21m00Tcm4TlvDq8ikWAM", "name": "Rachel",      "accent": "American English",   "gender": "female"},
    {"id": "AZnzlk1XvdvUeBnXmlld", "name": "Domi",        "accent": "American English",   "gender": "female"},
    {"id": "EXAVITQu4vr4xnSDxMaL", "name": "Bella",       "accent": "American English",   "gender": "female"},
    {"id": "ErXwobaYiN019PkySvjV", "name": "Antoni",      "accent": "American English",   "gender": "male"},
    {"id": "MF3mGyEYCl7XYWbV9V6O", "name": "Elli",        "accent": "American English",   "gender": "female"},
    {"id": "TxGEqnHWrfWFTfGW9XjX", "name": "Josh",        "accent": "American English",   "gender": "male"},
    {"id": "VR6AewLTigWG4xSOukaG", "name": "Arnold",      "accent": "American English",   "gender": "male"},
    {"id": "pNInz6obpgDQGcFmaJgB", "name": "Adam",        "accent": "American English",   "gender": "male"},
    {"id": "yoZ06aMxZJJ28mfd3POQ", "name": "Sam",         "accent": "American English",   "gender": "male"},
    {"id": "jBpfuIE2acCO8z3wKNLl", "name": "Glinda",      "accent": "American English",   "gender": "female"},
    {"id": "onwK4e9ZLuTAKqWW03F9", "name": "Daniel",      "accent": "British English",    "gender": "male"},
    {"id": "flq6f7yk4E4fJM5XTYuZ", "name": "Michael",     "accent": "American English",   "gender": "male"},
]


async def get_preset_voices() -> list[dict]:
    return PRESET_VOICES


PREVIEW_TEXT = "Hi! I'm your AI voice assistant, ready to bring your videos to life."


async def generate_tts_audio(voice_id: str, text: str, api_key: str | None = None) -> bytes:
    """Generate full TTS audio for the video pipeline. Returns MP3 bytes.

    `api_key` is the user's own ElevenLabs key when the voice config carries one
    (private voices are only reachable with their owner's key).

    Raises with the real status + body on failure (instead of silently returning
    None) so the job error is debuggable, and so the Celery task retries on
    transient errors like 429 rate limits.
    """
    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(
            f"{EL_BASE}/text-to-speech/{voice_id}",
            headers={**_headers(api_key), "Content-Type": "application/json"},
            json={
                "text": text,
                "model_id": settings.elevenlabs_model,
                "output_format": "mp3_44100_128",
            },
        )
    if resp.status_code == 200:
        return resp.content
    raise RuntimeError(
        f"ElevenLabs TTS failed for voice_id={voice_id} "
        f"(HTTP {resp.status_code}): {resp.text[:300]}"
    )


async def generate_preview_audio(voice_id: str, api_key: str | None = None) -> bytes | None:
    """Call ElevenLabs TTS with a short sample text and return audio bytes."""
    async with httpx.AsyncClient(timeout=20) as client:
        try:
            resp = await client.post(
                f"{EL_BASE}/text-to-speech/{voice_id}",
                headers={**_headers(api_key), "Content-Type": "application/json"},
                json={
                    "text": PREVIEW_TEXT,
                    "model_id": settings.elevenlabs_model,
                    "output_format": "mp3_44100_64",
                },
            )
            if resp.status_code == 200:
                return resp.content
            return None
        except httpx.HTTPError:
            return None


_PRESET_NAME_MAP = {v["id"]: v["name"] for v in PRESET_VOICES}

VALIDATE_TEXT = "Hello."


async def validate_voice_id(voice_id: str, api_key: str | None = None) -> dict:
    """Validate an ElevenLabs voice ID by attempting a minimal TTS call.

    Returns {"valid", "name", "voice_id", "reason"}. `reason` distinguishes the
    two common failure modes so the UI can explain what to do:
      - "not_accessible": the ID exists but isn't reachable with THIS key
        (private voice from another account → user must supply their own key)
      - "invalid_key": the supplied api_key itself was rejected
      - "invalid": anything else (malformed/unknown ID, network error)
    """
    preset_name = _PRESET_NAME_MAP.get(voice_id)

    async with httpx.AsyncClient(timeout=20) as client:
        try:
            resp = await client.post(
                f"{EL_BASE}/text-to-speech/{voice_id}",
                headers={**_headers(api_key), "Content-Type": "application/json"},
                json={
                    "text": VALIDATE_TEXT,
                    "model_id": settings.elevenlabs_model,
                    "output_format": "mp3_44100_64",
                },
            )
            if resp.status_code == 200:
                return {"valid": True, "name": preset_name, "voice_id": voice_id, "reason": None}
            body = resp.text or ""
            if resp.status_code == 401:
                reason = "invalid_key"
            elif "voice_not_found" in body or resp.status_code == 404:
                reason = "not_accessible"
            else:
                reason = "invalid"
            return {"valid": False, "name": None, "voice_id": voice_id, "reason": reason}
        except httpx.HTTPError:
            return {"valid": False, "name": None, "voice_id": voice_id, "reason": "invalid"}
