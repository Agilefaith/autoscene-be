import httpx
from openai import AsyncOpenAI
from app.core.config import get_settings

settings = get_settings()
_client = AsyncOpenAI(api_key=settings.openai_api_key)


async def transcribe(audio_path: str, vocabulary: list[str] | None = None) -> dict:
    """
    Transcribe audio with word-level timestamps using Whisper.
    Returns dict with 'text' and 'segments' (for subtitle burn-in).

    `vocabulary` seeds Whisper's prompt with proper nouns from the project (the
    cast names), so uncommon names are spelled correctly in the burned subtitles
    instead of being guessed phonetically ("Tunde" → "Tunda").
    """
    kwargs: dict = {}
    names = [v.strip() for v in (vocabulary or []) if v and v.strip()]
    if names:
        kwargs["prompt"] = "Names in this narration: " + ", ".join(dict.fromkeys(names)) + "."

    with open(audio_path, "rb") as f:
        response = await _client.audio.transcriptions.create(
            model="whisper-1",
            file=f,
            response_format="verbose_json",
            timestamp_granularities=["word"],
            **kwargs,
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
