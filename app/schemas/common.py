from pydantic import BaseModel
from typing import Literal

PlanTier = Literal["free", "pro", "premium"]
UserType = Literal["trial", "standard", "internal"]

# ── AutoScene ─────────────────────────────────────────────────────────────────
# Mode 1 = 1 image/scene (cinematic motion). Mode 2 = 3 images/scene (enhanced).
# Mode 3 (real animation) is deferred — see docs/AUTOSCENE_PRD.md §13.
RenderMode = Literal["mode_1", "mode_2"]

# AutoScene ships 16:9 (YouTube) and 9:16 (short-form) per PRD §4.7.
AutoSceneFormat = Literal["16:9", "9:16"]

# Per-scene camera motion (PRD §4.3). A richer vocabulary of cinematic camera
# moves — pans (all four directions), zooms, diagonals, and a combined move — so
# scenes don't feel repetitive. Chosen per scene by emotion (see motion_for_emotion).
SceneMotion = Literal[
    "zoom_in", "zoom_out", "pan_left", "pan_right", "pan_up", "pan_down",
    "zoom_in_tl", "zoom_in_br", "zoom_pan",
]
SCENE_MOTIONS: list[str] = [
    "zoom_in", "zoom_out", "pan_left", "pan_right", "pan_up", "pan_down",
    "zoom_in_tl", "zoom_in_br", "zoom_pan",
]

# Emotion → candidate camera moves. Calm scenes get slow pushes/pans; tense or
# dark scenes get more assertive diagonal zooms; upbeat scenes get lateral pans.
# Keys are matched as substrings against the scene's (lowercased) emotion.
_EMOTION_MOTIONS: dict[str, list[str]] = {
    "calm":        ["zoom_in", "pan_up", "zoom_out"],
    "peace":       ["zoom_in", "pan_up", "zoom_out"],
    "serene":      ["zoom_in", "pan_up"],
    "hope":        ["zoom_in", "pan_up", "zoom_pan"],
    "reflect":     ["zoom_out", "pan_down"],
    "sad":         ["zoom_out", "pan_down"],
    "somber":      ["zoom_out", "pan_down"],
    "tense":       ["zoom_in_br", "zoom_in_tl", "zoom_in"],
    "fear":        ["zoom_in_br", "zoom_in_tl"],
    "anger":       ["zoom_in_br", "pan_right", "zoom_in_tl"],
    "dark":        ["zoom_in_br", "zoom_in_tl", "pan_down"],
    "dramatic":    ["zoom_in_br", "zoom_in", "zoom_in_tl"],
    "excite":      ["pan_right", "pan_left", "zoom_pan"],
    "energetic":   ["pan_right", "pan_left", "zoom_pan"],
    "joy":         ["pan_right", "pan_left", "zoom_pan"],
    "inspire":     ["zoom_in", "zoom_pan", "pan_up"],
}


def motion_for_emotion(emotion: str, rng) -> str:
    """Pick a camera move suited to a scene's emotion (deterministic via rng).
    Falls back to the full motion set when the emotion is unknown."""
    e = (emotion or "").lower()
    for key, candidates in _EMOTION_MOTIONS.items():
        if key in e:
            return rng.choice(candidates)
    return rng.choice(SCENE_MOTIONS)

# SDXL v1 only accepts these exact dimension pairs. We pick the closest pair to the
# target aspect ratio, then crop/letterbox to the exact render canvas in FFmpeg.
SDXL_DIMENSIONS: dict[str, tuple[int, int]] = {
    "16:9": (1344, 768),
    "9:16": (768, 1344),
    "1:1":  (1024, 1024),
}

# Final render canvas (FFmpeg output) per format.
RENDER_DIMENSIONS: dict[str, tuple[int, int]] = {
    "16:9": (1920, 1080),
    "9:16": (1080, 1920),
    "1:1":  (1080, 1080),
}

# ── Visual styles (the ONLY four Faith specified) ─────────────────────────────
# Each maps to a strong prompt-style string (engine-agnostic; prepended to every
# scene image prompt so the whole video shares one look) + the closest SDXL
# style_preset for the Stability fallback. On Gemini the prompt text alone yields
# high fidelity (incl. Ghibli / Family Guy), so no preset is needed there.
STYLES: dict[str, dict] = {
    "stickman":   {"label": "Stickman",         "sdxl_preset": "line-art",
                   "prompt": "minimalist black stickman line drawing, simple stick figures with round heads, plain white background, hand-drawn doodle style, thin black lines"},
    "cartoon":    {"label": "Cartoon",          "sdxl_preset": "comic-book",
                   "prompt": "bold flat 2D cartoon illustration, thick clean black outlines, bright vibrant flat colors, playful cartoon style"},
    "ghibli":     {"label": "Ghibli",           "sdxl_preset": "anime",
                   "prompt": "Studio Ghibli anime style, soft hand-painted watercolor backgrounds, warm gentle lighting, whimsical detailed nature, cel-shaded characters"},
    "family_guy": {"label": "Family Guy Style", "sdxl_preset": "comic-book",
                   "prompt": "Family Guy adult-cartoon TV style, flat bold black outlines, simple rounded character shapes, flat cel shading, sitcom animation look"},
}
STYLE_IDS: list[str] = list(STYLES.keys())
DEFAULT_STYLE = "cartoon"


def style_prompt(style_id: str) -> str:
    """Strong style descriptor prepended to every image prompt ("" if unknown)."""
    return (STYLES.get(style_id) or {}).get("prompt", "")


def style_sdxl_preset(style_id: str) -> str | None:
    """Closest SDXL style_preset for the Stability fallback engine."""
    return (STYLES.get(style_id) or {}).get("sdxl_preset")


# Niches supported (PRD §5).
NICHES: list[str] = [
    "Bible storytelling", "Finance storytelling", "History", "Psychology",
    "Relatable Life Storytelling", "Money & Online Income",
    "Self-Improvement & Discipline", "Psychology & Human Behavior",
    "Dating & Relationships", "Dark Truths / Reality",
    "Motivational & Quote Shorts", "Educational Explainers",
    "What If / Hypothetical Scenarios",
]


class SubtitleSettings(BaseModel):
    enabled: bool = True
    font_color: str = "#FFFFFF"
    font_style: Literal["sans", "serif", "mono", "bold", "italic"] = "bold"
    font_size: int = 24
    placement: Literal["top", "center", "bottom"] = "bottom"
