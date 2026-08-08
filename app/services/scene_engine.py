"""Scene Breakdown Engine — AutoScene PRD §4.2.

Splits a narration script into ~10s scenes and writes a detailed SDXL image prompt
for each, aligned to that scene's content. For Mode 2 it additionally evolves each
prompt into a progressive A→B→C sequence (PRD §4.3 "Progressive Prompting").

This is the "one brain" that drives both modes — the same scene analysis, two
prompt shapes.
"""

import asyncio
import json
import re

from openai import AsyncOpenAI

from app.core.config import get_settings
from app.schemas.common import style_prompt
from app.services.character_sheet import cast_block

settings = get_settings()
_client = AsyncOpenAI(api_key=settings.openai_api_key)

# Quality boosters appended when a style has no dedicated style block.
_STYLE_SUFFIX = "highly detailed, sharp focus, professional lighting"

# Faith's prompt-engine "final lock line" — the last line of every image prompt.
_LOCK_LINE = ("The image must exactly match the narration with zero deviation, "
              "no extra elements, no missing elements. Single full-bleed image, one "
              "continuous scene, no split panels, no collage, no borders.")


# ── Deterministic script segmentation ─────────────────────────────────────────
# Scene boundaries used to be decided by the model, which routinely returned fewer
# scenes than asked for; the voiceover stage then spread the real audio over those
# few scenes and single images sat on screen for 15-26s (Faith, 2026-08-03). The
# split is now computed in code, so the scene count and the narration each scene
# carries are both exact, and every image is written for the words actually spoken
# over it.

# Niches Faith wants cut one image per two-line stanza (2026-08-03). Matched
# case-insensitively, and both spellings of "Behaviour/Behavior" are accepted.
STANZA_NICHES = {
    "psychology",
    "psychology & human behaviour",
    "psychology & human behavior",
    "relatable life storytelling",
}


def uses_stanza_split(niche: str) -> bool:
    return (niche or "").strip().lower() in STANZA_NICHES


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?…])\s+", (text or "").strip())
    return [p.strip() for p in parts if p.strip()]


def split_into_stanzas(script: str) -> list[str]:
    """One unit per two-line stanza — the shape of Faith's high-retention scripts.

    Blank-line separated stanzas are used as authored. A script without blank
    lines falls back to pairing consecutive lines, then to pairing sentences, so
    a pasted script never collapses into one giant scene.
    """
    blocks = [b.strip() for b in re.split(r"\n\s*\n", script or "") if b.strip()]
    if len(blocks) > 1:
        return blocks

    lines = [ln.strip() for ln in (script or "").splitlines() if ln.strip()]
    if len(lines) > 1:
        return [" ".join(lines[i:i + 2]) for i in range(0, len(lines), 2)]

    sents = _sentences(script)
    return [" ".join(sents[i:i + 2]) for i in range(0, len(sents), 2)] or [script.strip()]


def split_by_duration(script: str, scene_seconds: int) -> list[str]:
    """Sentence-aligned units, each holding at most one scene's worth of speech.

    A unit is capped at the words that fit in `scene_seconds` at the configured
    speaking rate, so no image can outstay its narration. A single sentence longer
    than the budget becomes its own unit rather than being cut mid-thought.
    """
    budget = max(4, round(scene_seconds * settings.script_words_per_minute / 60))
    units: list[str] = []
    cur: list[str] = []
    cur_words = 0
    for sent in _sentences(script):
        n = len(sent.split())
        if cur and cur_words + n > budget:
            units.append(" ".join(cur))
            cur, cur_words = [], 0
        cur.append(sent)
        cur_words += n
    if cur:
        units.append(" ".join(cur))
    return units or [(script or "").strip()]


def segment_script(script: str, *, niche: str, scene_seconds: int) -> list[str]:
    """Split a script into the exact units that become scenes."""
    units = (split_into_stanzas(script) if uses_stanza_split(niche)
             else split_by_duration(script, scene_seconds))
    return units[:settings.scene_max_count]


def _system_prompt(render_mode: str) -> str:
    base = (
        "You are AutoScene's scene-breakdown engine — an expert storyboard artist, "
        "cinematic director, and visual prompt engineer. You are given the scenes "
        "ALREADY SPLIT, in order, and you write one entry per scene using that scene's "
        "narration EXACTLY as supplied — never merge, reorder, split or reword them. "
        "For EACH scene you write a "
        "detailed, generation-ready text-to-image prompt that is a DIRECT visual "
        "representation of that scene's narration: every key noun and every action in "
        "the narration must be visible in the image; never invent elements that are "
        "not narrated and never omit important ones. Build each image prompt in this "
        "order: (1) shot type + main subject, (2) character description — every "
        "character keeps the SAME identity (face structure, hair, skin tone, body "
        "proportions) across ALL scenes so they are instantly recognizable in every "
        "frame, (3) the visible action, (4) clothing appropriate to the scene context "
        "(clothing may change per scene; identity never), (5) environment with "
        "foreground/midground/background depth, (6) all props the narration mentions "
        "and no others, (7) lighting (time of day, source direction, quality), "
        "(8) mood expressed visually through pose and composition, (9) camera framing "
        "and depth of field. Never include text, captions, logos, or watermarks in "
        "the image prompt. Each prompt must describe ONE single continuous moment "
        "from ONE camera position — never a split panel, diptych, collage, storyboard "
        "grid, or before/after layout; when a scene's narration spans two moments, "
        "pick the single most visual one. Output valid JSON only."
    )
    return base


def _user_prompt(units: list[str], niche: str, style_text: str, render_mode: str,
                 cast: str = "") -> str:
    style_line = f"Visual style for EVERY scene: {style_text}." if style_text else ""
    niche_line = f"Content niche: {niche}." if niche else ""
    cast_line = f"{cast}\n\n" if cast else ""
    shape = (
        '  {"emotion": str, "action": str, "environment": str, "image_prompt": str}'
    )
    note = "Each scene has a single detailed image_prompt."

    numbered = "\n".join(f"[{i + 1}] {u}" for i, u in enumerate(units))
    return (
        f"{style_line} {niche_line}\n\n"
        f"{cast_line}"
        f"Here are {len(units)} scenes, already split and in order. Write one entry for "
        f"EACH, in the same order, describing exactly what that scene's narration says. "
        f"Return EXACTLY {len(units)} entries. {note}\n\n"
        f"SCENES:\n{numbered}\n\n"
        'Output a JSON object: {"scenes": [\n'
        f"{shape}\n"
        "]}\n"
        "Output JSON only, no markdown."
    )


async def _prompts_for_units(
    units: list[str],
    *,
    render_mode: str,
    niche: str,
    style_text: str,
    cast: str = "",
) -> list[dict]:
    """One OpenAI call: write the prompt entry for each pre-split scene unit.

    The model no longer decides scene boundaries — it only describes the units it
    is handed — so the scene count and the narration per scene are exact. Batches
    are bounded by the caller so a response never overflows the output-token cap
    and truncates the JSON. `cast` (the identity-lock block) goes to EVERY batch,
    or a character introduced in one batch drifts in the next.
    """
    response = await _client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": _system_prompt(render_mode)},
            {"role": "user", "content": _user_prompt(units, niche, style_text, render_mode, cast)},
        ],
        temperature=0.6,
        max_tokens=16000,
        response_format={"type": "json_object"},
    )
    data = json.loads(response.choices[0].message.content or "{}")
    entries = data.get("scenes") or []
    # Pad/trim so the caller always gets one entry per unit even if the model
    # miscounts — the unit text itself is the source of truth for scene_text.
    entries = entries[:len(units)]
    while len(entries) < len(units):
        entries.append({})
    return entries


def _decorate(prompt: str, style_text: str) -> str:
    """Embed the chosen style block at the END of the prompt (Faith's prompt-engine
    spec: scene description first, style block appended, lock line last)."""
    p = (prompt or "").strip().rstrip(".")
    style = (style_text or _STYLE_SUFFIX).strip().rstrip(".")
    return f"{p}. {style}. {_LOCK_LINE}"


async def breakdown_script(
    script: str,
    *,
    render_mode: str = "mode_1",
    niche: str = "",
    style: str = "",
    duration_seconds: int = 60,
    scene_duration: int | None = None,
    characters: list[dict] | None = None,
) -> list[dict]:
    """Return a list of scene dicts ready to insert into the `scenes` table.

    Each dict: idx, scene_text, emotion, action, environment, image_prompt,
    duration_seconds.

    `characters` is the project's named cast (see services/character_sheet.py);
    their identity-lock block is shared by every breakdown call.
    """
    scene_dur = scene_duration or settings.scene_duration_seconds
    style_text = style_prompt(style)  # strong style descriptor for this style id
    cast = cast_block(characters or [])

    # Scene boundaries are decided here, not by the model: stanza units for the
    # niches Faith wants cut per two-line stanza, otherwise sentence-aligned units
    # that each hold at most one scene's worth of narration.
    units = segment_script(script, niche=niche, scene_seconds=scene_dur)

    # Bound units-per-call so one response never overflows the model's output-token
    # cap (which truncates the JSON mid-string).
    per_call = settings.scene_breakdown_batch
    batches_of_units = [units[i:i + per_call] for i in range(0, len(units), per_call)]

    batches = await asyncio.gather(*(
        _prompts_for_units(batch, render_mode=render_mode, niche=niche,
                           style_text=style_text, cast=cast)
        for batch in batches_of_units
    ))
    raw_scenes = [s for batch in batches for s in batch]

    scenes: list[dict] = []
    for i, (unit, s) in enumerate(zip(units, raw_scenes)):
        base_prompt = _decorate(s.get("image_prompt", ""), style_text)
        scene: dict = {
            "idx": i,
            # The unit is authoritative — the model describes it, it never rewrites it.
            "scene_text": unit.strip(),
            "emotion": (s.get("emotion") or "").strip(),
            "action": (s.get("action") or "").strip(),
            "environment": (s.get("environment") or "").strip(),
            "image_prompt": base_prompt,
            "duration_seconds": scene_dur,
        }
        scenes.append(scene)

    return scenes
