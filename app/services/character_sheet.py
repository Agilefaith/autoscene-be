"""Character identity lock — builds a reusable "character sheet" per cast member.

The user uploads one reference image per character and names them (Leah, David,
Ruth…). This module turns each reference into a dense, fixed physical description
via GPT-Vision. That description is then injected verbatim into EVERY scene's
image prompt across the whole video, so a character is described identically in
scene 1 and scene 40 — which is what keeps them recognizable.

Per Faith's identity-lock spec (docs, 2026-07-17) the sheet captures only the
IMMUTABLE features (face structure, hair, skin tone, build). Clothing is
deliberately excluded: it must change per scene context while identity does not.
"""

import asyncio

from openai import AsyncOpenAI

from app.core.config import get_settings

settings = get_settings()
_client = AsyncOpenAI(api_key=settings.openai_api_key)

_SYSTEM = (
    "You are an art director writing character-design notes for an illustrator. "
    "The images you receive are character-design references for a stylised animated "
    "video, and your notes let a different artist redraw the same character "
    "consistently from scene to scene."
)

_SHEET_INSTRUCTION = (
    "Write the character-design notes for the character in this reference, as ONE "
    "dense sentence (max 45 words) covering: apparent age range, gender presentation, "
    "face shape, eye shape and colour, nose and mouth shape, skin tone, hair colour/"
    "length/texture/style, build and height impression, and any consistent design "
    "feature (freckles, beard, glasses). "
    "Do NOT describe clothing, background, pose, expression, lighting, or mood — "
    "those change per scene. No names, no preamble, no markdown."
)

# A vision model may decline to describe a person's appearance (most often when the
# reference is a photo of a real person rather than a character illustration). The
# reply then reads as an apology, not a description — which must never be mistaken
# for a character sheet and pasted into image prompts.
_REFUSAL_MARKERS = (
    "i'm sorry", "i am sorry", "i can't", "i cannot", "i won't", "i will not",
    "unable to", "can't help", "cannot help", "not able to",
)
_MIN_SHEET_CHARS = 40


def _is_usable_sheet(text: str) -> bool:
    """True if the reply is a real description rather than a refusal or a stub."""
    t = (text or "").strip()
    if len(t) < _MIN_SHEET_CHARS:
        return False
    return not any(marker in t[:80].lower() for marker in _REFUSAL_MARKERS)


async def _describe_one(image_url: str) -> str | None:
    """GPT-Vision → one locked identity sentence, or None if no usable sheet came
    back. None is deliberate: that character then takes no part in the by-name
    identity lock, instead of poisoning every image prompt with a refusal string."""
    try:
        resp = await _client.chat.completions.create(
            model="gpt-4o",  # full 4o: design detail from an image needs the stronger vision model
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": [
                    {"type": "text", "text": _SHEET_INSTRUCTION},
                    {"type": "image_url", "image_url": {"url": image_url}},
                ]},
            ],
            temperature=0.2,
            max_tokens=120,
        )
        sheet = (resp.choices[0].message.content or "").strip()
        return sheet if _is_usable_sheet(sheet) else None
    except Exception:
        return None


async def build_character_sheets(characters: list[dict]) -> list[dict]:
    """Fill in `description` for every character that doesn't have one yet.

    Input/output shape: [{"name", "image_url", "description"}]. Characters that
    already carry a description are passed through untouched, so re-running the
    breakdown never re-pays for vision calls.
    """
    todo = [c for c in characters if c.get("image_url") and not (c.get("description") or "").strip()]
    if todo:
        descriptions = await asyncio.gather(*(_describe_one(c["image_url"]) for c in todo))
        for character, description in zip(todo, descriptions):
            character["description"] = description
    return characters


def unlocked_names(characters: list[dict]) -> list[str]:
    """Named characters that ended up WITHOUT a usable sheet — they get no by-name
    identity lock, so the caller should surface this rather than degrade quietly."""
    return [
        c["name"] for c in characters
        if (c.get("name") or "").strip() and not (c.get("description") or "").strip()
    ]


def cast_block(characters: list[dict]) -> str:
    """Render the cast as a prompt block injected into EVERY scene-breakdown call.

    This is the fix for long scripts: the breakdown runs as several parallel
    OpenAI calls, and without a shared cast block each call would invent its own
    look for the same character, so the cast drifted between chunks.
    """
    named = [
        c for c in characters
        if (c.get("name") or "").strip() and (c.get("description") or "").strip()
    ]
    if not named:
        return ""
    lines = "\n".join(f"- {c['name']}: {c['description']}" for c in named)
    return (
        "CAST — IDENTITY LOCK (HIGHEST PRIORITY, applies to EVERY scene):\n"
        f"{lines}\n"
        "Whenever one of these characters appears in a scene, refer to them BY NAME "
        "and restate their locked physical description above VERBATIM inside that "
        "scene's image_prompt. Their face, hair, skin tone, and body proportions must "
        "be identical in every scene — only clothing, pose, expression, camera angle, "
        "and lighting may change with the scene's context. Never redesign, reinterpret, "
        "or 'improve' a character, and never invent a new character that the narration "
        "does not mention."
    )
