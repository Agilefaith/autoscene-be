import httpx
from app.core.config import get_settings

settings = get_settings()
EL_BASE = "https://api.elevenlabs.io/v1"


def _headers() -> dict:
    return {"xi-api-key": settings.elevenlabs_api_key}


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


async def generate_tts_audio(voice_id: str, text: str) -> bytes:
    """Generate full TTS audio for the video pipeline. Returns MP3 bytes.

    Raises with the real status + body on failure (instead of silently returning
    None) so the job error is debuggable, and so the Celery task retries on
    transient errors like 429 rate limits.
    """
    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(
            f"{EL_BASE}/text-to-speech/{voice_id}",
            headers={**_headers(), "Content-Type": "application/json"},
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


async def generate_preview_audio(voice_id: str) -> bytes | None:
    """Call ElevenLabs TTS with a short sample text and return audio bytes."""
    async with httpx.AsyncClient(timeout=20) as client:
        try:
            resp = await client.post(
                f"{EL_BASE}/text-to-speech/{voice_id}",
                headers={**_headers(), "Content-Type": "application/json"},
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


async def validate_voice_id(voice_id: str) -> dict:
    """Validate an ElevenLabs voice ID by attempting a minimal TTS call."""
    preset_name = _PRESET_NAME_MAP.get(voice_id)

    async with httpx.AsyncClient(timeout=20) as client:
        try:
            resp = await client.post(
                f"{EL_BASE}/text-to-speech/{voice_id}",
                headers={**_headers(), "Content-Type": "application/json"},
                json={
                    "text": VALIDATE_TEXT,
                    "model_id": settings.elevenlabs_model,
                    "output_format": "mp3_44100_64",
                },
            )
            if resp.status_code == 200:
                return {"valid": True, "name": preset_name, "voice_id": voice_id}
            return {"valid": False, "name": None, "voice_id": voice_id}
        except httpx.HTTPError:
            return {"valid": False, "name": None, "voice_id": voice_id}
