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
