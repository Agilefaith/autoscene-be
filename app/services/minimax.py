import httpx
from app.core.config import get_settings

settings = get_settings()
MM_BASE = "https://api.minimax.io/v1"
MM_MODEL = "speech-02-hd"

VALIDATE_TEXT = "Hello."
PREVIEW_TEXT = "Hi! I'm your AI voice assistant, ready to bring your videos to life."


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {settings.minimax_api_key}",
        "Content-Type": "application/json",
    }


def _tts_body(voice_id: str, text: str) -> dict:
    return {
        "model": MM_MODEL,
        "text": text,
        "voice_setting": {"voice_id": voice_id, "speed": 1.0, "vol": 1.0},
        "output_format": "hex",
    }


async def validate_voice_id(voice_id: str) -> dict:
    """Validate a Minimax voice ID by attempting a minimal TTS call."""
    async with httpx.AsyncClient(timeout=20) as client:
        try:
            resp = await client.post(
                f"{MM_BASE}/t2a_v2",
                headers=_headers(),
                json=_tts_body(voice_id, VALIDATE_TEXT),
            )
            if resp.status_code != 200:
                return {"valid": False, "voice_id": voice_id}
            body = resp.json()
            status_code = body.get("base_resp", {}).get("status_code", -1)
            if status_code == 0:
                return {"valid": True, "voice_id": voice_id}
            return {"valid": False, "voice_id": voice_id}
        except httpx.HTTPError:
            return {"valid": False, "voice_id": voice_id}


async def generate_tts_audio(voice_id: str, text: str) -> bytes | None:
    """Generate full TTS audio for video pipeline. Returns MP3 bytes (decoded from hex)."""
    async with httpx.AsyncClient(timeout=120) as client:
        try:
            resp = await client.post(
                f"{MM_BASE}/t2a_v2",
                headers=_headers(),
                json=_tts_body(voice_id, text),
            )
            if resp.status_code != 200:
                return None
            body = resp.json()
            if body.get("base_resp", {}).get("status_code", -1) != 0:
                return None
            hex_audio = body.get("data", {}).get("audio", "")
            return bytes.fromhex(hex_audio) if hex_audio else None
        except (httpx.HTTPError, ValueError):
            return None


async def generate_preview_audio(voice_id: str) -> bytes | None:
    """Generate a short TTS preview and return audio bytes (decoded from hex)."""
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            resp = await client.post(
                f"{MM_BASE}/t2a_v2",
                headers=_headers(),
                json=_tts_body(voice_id, PREVIEW_TEXT),
            )
            if resp.status_code != 200:
                return None
            body = resp.json()
            status_code = body.get("base_resp", {}).get("status_code", -1)
            if status_code != 0:
                return None
            hex_audio = body.get("data", {}).get("audio", "")
            if not hex_audio:
                return None
            return bytes.fromhex(hex_audio)
        except (httpx.HTTPError, ValueError):
            return None
