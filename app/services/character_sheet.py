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


# Fallback framing, used when the first attempt comes back as a refusal. Vision
# refusals here are INTERMITTENT (the same reference can succeed on a retry), and
# they happen most often on photoreal references. This wording asks only for
# generic drawable attributes and never for anyone's identity, which clears the
# refusal in practice.
_NEUTRAL_SYSTEM = (
    "You write visual style notes for animation production. You never identify or name "
    "real individuals; you only note generic visual attributes so an illustrator can draw "
    "a consistent fictional character."
)
_NEUTRAL_INSTRUCTION = (
    "This is a character-design reference for an animated video. In ONE sentence "
    "(max 45 words), note only the generic visual attributes an illustrator needs to keep "
    "the drawing consistent: apparent age range, gender presentation, general face shape, "
    "eye colour, skin tone, hair colour/length/texture, and build. Do not identify anyone, "
    "and do not describe clothing, background, pose, or mood. No preamble."
)


async def _ask_vision(image_url: str, system: str, instruction: str) -> str:
    resp = await _client.chat.completions.create(
        model="gpt-4o",  # full 4o: design detail from an image needs the stronger vision model
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": [
                {"type": "text", "text": instruction},
                {"type": "image_url", "image_url": {"url": image_url}},
            ]},
        ],
        temperature=0.2,
        max_tokens=120,
    )
    return (resp.choices[0].message.content or "").strip()


async def _describe_one(image_url: str) -> str | None:
    """GPT-Vision → one locked identity sentence, or None if every attempt failed.

    Tries the art-director framing first, then a neutral attributes-only framing.
    The retry matters: a refusal from the vision model is intermittent, and a
    character that ends up without a sheet loses its per-scene feature lock (it
    still keeps a by-name + reference-image lock — see cast_block).
    """
    attempts = (
        (_SYSTEM, _SHEET_INSTRUCTION),
        (_NEUTRAL_SYSTEM, _NEUTRAL_INSTRUCTION),
        (_NEUTRAL_SYSTEM, _NEUTRAL_INSTRUCTION),  # refusals are flaky; one more roll
    )
    for system, instruction in attempts:
        try:
            sheet = await _ask_vision(image_url, system, instruction)
            if _is_usable_sheet(sheet):
                return sheet
        except Exception:
            continue
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
    """Named characters that ended up WITHOUT a usable sheet. They still get a
    by-name + reference-image lock (see cast_block), but not the per-scene feature
    restatement, so the caller should surface this rather than degrade quietly."""
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
    named = [c for c in characters if (c.get("name") or "").strip()]
    if not named:
        return ""
    # A character whose sheet couldn't be built still belongs in the block. Dropping
    # it left the scene engine free to re-invent that character every scene (the
    # exact drift Faith reported for "Ada"); naming it here keeps it anchored to its
    # reference image, which the image engine receives labelled by name.
    lines = "\n".join(
        f"- {c['name']}: {c['description'].strip()}" if (c.get("description") or "").strip()
        else (f"- {c['name']}: (see the reference image supplied for {c['name']}) — keep this "
              f"character's face, hair, skin tone and build EXACTLY as in that reference, "
              f"identical in every scene")
        for c in named
    )
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
