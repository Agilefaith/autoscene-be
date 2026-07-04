"""Scene Breakdown Engine — AutoScene PRD §4.2.

Splits a narration script into ~10s scenes and writes a detailed SDXL image prompt
for each, aligned to that scene's content. For Mode 2 it additionally evolves each
prompt into a progressive A→B→C sequence (PRD §4.3 "Progressive Prompting").

This is the "one brain" that drives both modes — the same scene analysis, two
prompt shapes.
"""

import json
import math

from openai import AsyncOpenAI

from app.core.config import get_settings

settings = get_settings()
_client = AsyncOpenAI(api_key=settings.openai_api_key)

# A scene's image prompt should always carry these so SDXL output stays on-style
# and consistent across scenes.
_STYLE_SUFFIX = "highly detailed, professional, sharp focus, dramatic lighting"


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


def _user_prompt(script: str, niche: str, style: str, n_scenes: int, render_mode: str, character: str = "") -> str:
    style_line = f"Visual style: {style}." if style else "Visual style: cinematic, photographic."
    niche_line = f"Content niche: {niche}." if niche else ""
    character_line = (
        f"Main character (keep IDENTICAL in every scene): {character}." if character else ""
    )
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
        f"{style_line} {niche_line} {character_line}\n\n"
        f"Split this script into EXACTLY {n_scenes} sequential scenes that together "
        f"cover the whole script in order. Assign each scene the portion of the "
        f"narration it illustrates (scene_text). {note}\n\n"
        f"SCRIPT:\n{script}\n\n"
        'Output a JSON object: {"scenes": [\n'
        f"{shape}\n"
        "]}\n"
        "Output JSON only, no markdown."
    )


def _decorate(prompt: str, style: str, character: str = "") -> str:
    """Prepend the fixed character description (best-effort identity lock) and
    append the style suffix, keeping prompts SDXL-friendly."""
    p = (prompt or "").strip().rstrip(".")
    char = f"{character.strip().rstrip('.')}. " if character else ""
    extra = f", {style}" if style else ""
    return f"{char}{p}{extra}, {_STYLE_SUFFIX}"


async def breakdown_script(
    script: str,
    *,
    render_mode: str = "mode_1",
    niche: str = "",
    style: str = "",
    character_desc: str = "",
    duration_seconds: int = 60,
    scene_duration: int | None = None,
) -> list[dict]:
    """Return a list of scene dicts ready to insert into the `scenes` table.

    Each dict: idx, scene_text, emotion, action, environment, image_prompt,
    image_prompts (mode_2 only), duration_seconds.
    """
    scene_dur = scene_duration or settings.scene_duration_seconds
    n_scenes = target_scene_count(duration_seconds, scene_dur)

    response = await _client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": _system_prompt(render_mode)},
            {"role": "user", "content": _user_prompt(script, niche, style, n_scenes, render_mode, character_desc)},
        ],
        temperature=0.6,
        response_format={"type": "json_object"},
    )
    data = json.loads(response.choices[0].message.content or "{}")
    raw_scenes = data.get("scenes") or []

    scenes: list[dict] = []
    for i, s in enumerate(raw_scenes):
        base_prompt = _decorate(s.get("image_prompt", ""), style, character_desc)
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
            progressive = [(_decorate(p, style, character_desc)) for p in progressive if p][:3]
            while len(progressive) < 3:
                progressive.append(base_prompt)
            scene["image_prompts"] = progressive
        scenes.append(scene)

    return scenes
