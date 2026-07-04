import httpx
from openai import AsyncOpenAI
from app.core.config import get_settings

settings = get_settings()
_client = AsyncOpenAI(api_key=settings.openai_api_key)


async def transcribe(audio_path: str) -> dict:
    """
    Transcribe audio with word-level timestamps using Whisper.
    Returns dict with 'text' and 'segments' (for subtitle burn-in).
    """
    with open(audio_path, "rb") as f:
        response = await _client.audio.transcriptions.create(
            model="whisper-1",
            file=f,
            response_format="verbose_json",
            timestamp_granularities=["word"],
        )

    return {
        "text": response.text,
        "segments": [
            {
                "word": w.word,
                "start": w.start,
                "end": w.end,
            }
            for w in (response.words or [])
        ],
    }
