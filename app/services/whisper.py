import re

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

    words = [{"word": w.word, "start": w.start, "end": w.end} for w in (response.words or [])]
    return {
        "text": response.text,
        "segments": _restore_punctuation(words, response.text or ""),
    }


def _restore_punctuation(words: list[dict], text: str) -> list[dict]:
    """Re-attach punctuation from the full transcript onto the word timings.

    Whisper's word-level output strips punctuation ("below", not "below."), while
    the full text keeps it. Subtitle cues break on sentence ends, so without this
    a cue runs straight through a full stop and reads "meet One winter a"
    (Faith, 2026-08-03). Timings are untouched; only the text gains its
    punctuation back. Falls back to the unpunctuated words if the two streams
    drift apart.
    """
    tokens = text.split()
    if not words or not tokens:
        return words

    def bare(s: str) -> str:
        return re.sub(r"[^\w']+", "", s).lower()

    out: list[dict] = []
    ti = 0
    for w in words:
        target = bare(w["word"])
        # tolerate the odd extra/missing token between the two streams
        for probe in range(ti, min(ti + 3, len(tokens))):
            if bare(tokens[probe]) == target:
                w = {**w, "word": tokens[probe]}
                ti = probe + 1
                break
        out.append(w)
    return out
