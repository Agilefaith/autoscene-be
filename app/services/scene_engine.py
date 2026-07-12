"""Scene Breakdown Engine — AutoScene PRD §4.2.

Splits a narration script into ~10s scenes and writes a detailed SDXL image prompt
for each, aligned to that scene's content. For Mode 2 it additionally evolves each
prompt into a progressive A→B→C sequence (PRD §4.3 "Progressive Prompting").

This is the "one brain" that drives both modes — the same scene analysis, two
prompt shapes.
"""

import asyncio
import json
import math
import re

from openai import AsyncOpenAI

from app.core.config import get_settings
from app.schemas.common import style_prompt

settings = get_settings()
_client = AsyncOpenAI(api_key=settings.openai_api_key)

# Quality boosters appended to every prompt.
_STYLE_SUFFIX = "highly detailed, sharp focus, professional lighting"


def target_scene_count(duration_seconds: int, scene_duration: int) -> int:
    n = math.ceil(max(1, duration_seconds) / max(1, scene_duration))
    return max(settings.scene_min_count, min(settings.scene_max_count, n))


def _system_prompt(render_mode: str) -> str:
    base = (
        "You are AutoScene's scene-breakdown engine. You split a narration script "
        "into sequential scenes of roughly equal length and, for EACH scene, write "
        "a detailed text-to-image prompt (for Stability AI SDXL) that visually "
        "matches exactly what that scene narrates. Image prompts must be concrete and "
        "cinematic: describe subject, setting, composition, lighting, and mood. "
        "Never include text, captions, logos, or watermarks in the image prompt. "
        "Keep the SAME main character description and environment consistent across "
        "scenes for visual continuity. Output valid JSON only."
    )
    if render_mode == "mode_2":
        base += (
            " For each scene also produce THREE progressive image prompts (A, B, C) "
            "showing slight motion progression within the scene (e.g. standing → "
            "turning → walking). A/B/C must keep the same character identity and "
            "environment; only pose/angle/action advances slightly."
        )
    return base


def _user_prompt(script: str, niche: str, style_text: str, n_scenes: int, render_mode: str) -> str:
    style_line = f"Visual style for EVERY scene: {style_text}." if style_text else ""
    niche_line = f"Content niche: {niche}." if niche else ""
    if render_mode == "mode_2":
        shape = (
            '  {"scene_text": str, "emotion": str, "action": str, "environment": str, '
            '"image_prompt": str, "image_prompts": [str, str, str]}'
        )
        note = '"image_prompts" must be exactly 3 progressive prompts (A,B,C).'
    else:
        shape = (
            '  {"scene_text": str, "emotion": str, "action": str, "environment": str, '
            '"image_prompt": str}'
        )
        note = "Each scene has a single detailed image_prompt."

    return (
        f"{style_line} {niche_line}\n\n"
        f"Split this script into EXACTLY {n_scenes} sequential scenes that together "
        f"cover the whole script in order. Assign each scene the portion of the "
        f"narration it illustrates (scene_text). {note}\n\n"
        f"SCRIPT:\n{script}\n\n"
        'Output a JSON object: {"scenes": [\n'
        f"{shape}\n"
        "]}\n"
        "Output JSON only, no markdown."
    )


def _split_script(script: str, parts: int) -> list[str]:
    """Split a script into `parts` roughly-equal chunks on sentence boundaries, so
    each chunk can be broken down in its own OpenAI call. Never splits mid-sentence."""
    text = script.strip()
    if parts <= 1:
        return [text]
    sentences = re.split(r"(?<=[.!?])\s+", text)
    target = (sum(len(s) for s in sentences) or 1) / parts
    chunks: list[str] = []
    cur: list[str] = []
    cur_len = 0
    for s in sentences:
        cur.append(s)
        cur_len += len(s)
        if cur_len >= target and len(chunks) < parts - 1:
            chunks.append(" ".join(cur))
            cur, cur_len = [], 0
    if cur:
        chunks.append(" ".join(cur))
    return chunks or [text]


def _distribute_counts(total: int, chunks: list[str]) -> list[int]:
    """Split `total` target scenes across chunks proportional to their length, with
    at least 1 per chunk and summing exactly to `total`."""
    lengths = [len(c) for c in chunks]
    tot = sum(lengths) or 1
    counts = [max(1, round(total * length / tot)) for length in lengths]
    i = 0
    while sum(counts) != total and counts:
        j = i % len(counts)
        if sum(counts) < total:
            counts[j] += 1
        elif counts[j] > 1:
            counts[j] -= 1
        i += 1
    return counts


async def _breakdown_chunk(
    script_chunk: str,
    n_scenes: int,
    *,
    render_mode: str,
    niche: str,
    style_text: str,
) -> list[dict]:
    """One OpenAI call: break a single script chunk into exactly `n_scenes` raw
    scene dicts. `max_tokens` is capped and the batch is bounded by the caller so a
    response never overflows the model's output limit and truncates the JSON."""
    response = await _client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": _system_prompt(render_mode)},
            {"role": "user", "content": _user_prompt(script_chunk, niche, style_text, n_scenes, render_mode)},
        ],
        temperature=0.6,
        max_tokens=16000,
        response_format={"type": "json_object"},
    )
    data = json.loads(response.choices[0].message.content or "{}")
    return data.get("scenes") or []


def _decorate(prompt: str, style_text: str) -> str:
    """Prepend the chosen style STRONGLY (so the whole video shares one look) and
    append quality boosters. Style is enforced at the front of every prompt."""
    p = (prompt or "").strip().rstrip(".")
    prefix = f"{style_text.strip().rstrip('.')}. " if style_text else ""
    return f"{prefix}{p}, {_STYLE_SUFFIX}"


async def breakdown_script(
    script: str,
    *,
    render_mode: str = "mode_1",
    niche: str = "",
    style: str = "",
    duration_seconds: int = 60,
    scene_duration: int | None = None,
) -> list[dict]:
    """Return a list of scene dicts ready to insert into the `scenes` table.

    Each dict: idx, scene_text, emotion, action, environment, image_prompt,
    image_prompts (mode_2 only), duration_seconds.
    """
    scene_dur = scene_duration or settings.scene_duration_seconds
    n_scenes = target_scene_count(duration_seconds, scene_dur)
    style_text = style_prompt(style)  # strong style descriptor for this style id

    # Bound scenes-per-call so one response never overflows the model's output-token
    # cap (which truncates the JSON mid-string). Long scripts are split into several
    # chunks, each broken down in its own call, then merged and re-indexed. Mode 2
    # emits 3 prompts/scene, so it packs fewer scenes per call.
    per_call = settings.scene_breakdown_batch
    if render_mode == "mode_2":
        per_call = max(1, per_call // 2)
    n_batches = max(1, math.ceil(n_scenes / per_call))

    if n_batches == 1:
        chunks, counts = [script.strip()], [n_scenes]
    else:
        chunks = _split_script(script, n_batches)
        counts = _distribute_counts(n_scenes, chunks)

    batches = await asyncio.gather(*(
        _breakdown_chunk(chunk, cnt, render_mode=render_mode, niche=niche, style_text=style_text)
        for chunk, cnt in zip(chunks, counts)
    ))
    raw_scenes = [s for batch in batches for s in batch]

    scenes: list[dict] = []
    for i, s in enumerate(raw_scenes):
        base_prompt = _decorate(s.get("image_prompt", ""), style_text)
        scene: dict = {
            "idx": i,
            "scene_text": (s.get("scene_text") or "").strip(),
            "emotion": (s.get("emotion") or "").strip(),
            "action": (s.get("action") or "").strip(),
            "environment": (s.get("environment") or "").strip(),
            "image_prompt": base_prompt,
            "duration_seconds": scene_dur,
        }
        if render_mode == "mode_2":
            progressive = s.get("image_prompts") or []
            # Pad/repair to exactly 3 prompts so the renderer always has A/B/C.
            progressive = [(_decorate(p, style_text)) for p in progressive if p][:3]
            while len(progressive) < 3:
                progressive.append(base_prompt)
            scene["image_prompts"] = progressive
        scenes.append(scene)

    return scenes
