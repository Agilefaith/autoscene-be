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


def _bare(s: str) -> str:
    return re.sub(r"[^\w']+", "", s or "").lower()


def scene_durations_from_transcript(
    scenes: list[dict], segments: list[dict], audio_seconds: float,
    min_scene_seconds: float = 2.0,
) -> list[float]:
    """Exact per-scene duration from Whisper's word-level timings, not an
    estimate.

    The previous approach split the audio's total duration across scenes
    proportional to each scene's word count, assuming a constant speaking rate.
    Real narration doesn't read at a constant rate — short, punchy lines (common
    in Faith's storytelling niches) are spoken slower per word than description,
    so that estimate drifted several seconds ahead of the real narration by the
    middle of a long video: images kept advancing on the assumed schedule while
    the actual voice was still catching up (Faith, 2026-08-16).

    Scene i's duration is instead the real gap between where its narration
    starts and where the next scene's starts (the last scene runs to the end of
    the audio) — so scene boundaries land exactly where they were actually
    spoken, with zero accumulated error regardless of video length.
    """
    starts = _scene_start_times(scenes, segments)
    durations: list[float] = []
    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else max(audio_seconds, start)
        durations.append(max(min_scene_seconds, round(end - start, 2)))
    return durations


def _scene_start_times(scenes: list[dict], segments: list[dict]) -> list[float]:
    """The timestamp each scene's narration actually starts at.

    Walks the transcript's words in order and matches each scene's own first
    few words against them, advancing a pointer roughly by that scene's word
    count — the same tolerant, small-lookahead matching _restore_punctuation
    uses for subtitles, so an occasional word Whisper drops or adds doesn't
    permanently desync everything after it. Falls back to holding the previous
    scene's start (never runs backward, never crashes) when a scene's words
    can't be found at all.
    """
    si = 0
    starts: list[float] = []
    for scene in scenes:
        tokens = [_bare(w) for w in (scene.get("scene_text") or "").split() if _bare(w)]
        if not tokens or not segments:
            starts.append(starts[-1] if starts else 0.0)
            continue

        target = tokens[0]
        found: float | None = None
        for probe in range(si, min(si + 6, len(segments))):
            if _bare(segments[probe]["word"]) == target:
                si = probe
                found = segments[probe]["start"]
                break
        if found is None:
            found = segments[si]["start"] if si < len(segments) else starts[-1] if starts else 0.0

        prev = starts[-1] if starts else 0.0
        starts.append(max(found, prev))  # scenes never run out of order
        si = min(si + len(tokens), len(segments))
    return starts


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

    out: list[dict] = []
    ti = 0
    for w in words:
        target = _bare(w["word"])
        # tolerate the odd extra/missing token between the two streams
        for probe in range(ti, min(ti + 3, len(tokens))):
            if _bare(tokens[probe]) == target:
                w = {**w, "word": tokens[probe]}
                ti = probe + 1
                break
        out.append(w)
    return out
