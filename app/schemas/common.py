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

# Per-scene camera motion (PRD §4.3 + Faith 2026-07-04: "a wide range of motion
# effects, camera angles, and editing techniques"). Pans in four directions,
# zooms in/out (centered and diagonal), combined moves, plus slower "editorial"
# moves — micro-drift, crane, breathing pulse, and a subtle rotation. Chosen per
# scene by emotion (see motion_for_emotion).
SceneMotion = Literal[
    "zoom_in", "zoom_out", "pan_left", "pan_right", "pan_up", "pan_down",
    "zoom_in_tl", "zoom_in_br", "zoom_pan",
    "zoom_out_tl", "zoom_out_br", "slow_drift", "crane_up", "pulse_in", "rotate_zoom",
]
SCENE_MOTIONS: list[str] = [
    "zoom_in", "zoom_out", "pan_left", "pan_right", "pan_up", "pan_down",
    "zoom_in_tl", "zoom_in_br", "zoom_pan",
    "zoom_out_tl", "zoom_out_br", "slow_drift", "crane_up", "pulse_in", "rotate_zoom",
]

# Emotion → candidate camera moves. Calm scenes get slow pushes/drifts; tense or
# dark scenes get more assertive diagonal zooms; upbeat scenes get lateral pans.
# Keys are matched as substrings against the scene's (lowercased) emotion.
_EMOTION_MOTIONS: dict[str, list[str]] = {
    "calm":        ["slow_drift", "zoom_in", "pan_up", "zoom_out"],
    "peace":       ["slow_drift", "zoom_in", "pan_up"],
    "serene":      ["slow_drift", "zoom_in", "pan_up"],
    "hope":        ["pulse_in", "zoom_in", "pan_up", "zoom_pan"],
    "reflect":     ["crane_up", "zoom_out", "pan_down", "slow_drift"],
    "sad":         ["crane_up", "zoom_out", "pan_down"],
    "somber":      ["crane_up", "zoom_out", "zoom_out_br"],
    "tense":       ["zoom_in_br", "zoom_in_tl", "rotate_zoom", "zoom_in"],
    "fear":        ["zoom_in_br", "zoom_in_tl", "rotate_zoom"],
    "anger":       ["zoom_in_br", "pan_right", "zoom_in_tl"],
    "dark":        ["zoom_in_br", "zoom_in_tl", "zoom_out_tl", "pan_down"],
    "dramatic":    ["zoom_in_br", "rotate_zoom", "zoom_in", "zoom_in_tl"],
    "excite":      ["pan_right", "pan_left", "zoom_pan", "pulse_in"],
    "energetic":   ["pan_right", "pan_left", "zoom_pan"],
    "joy":         ["pan_right", "pan_left", "pulse_in", "zoom_pan"],
    "inspire":     ["pulse_in", "zoom_in", "zoom_pan", "pan_up"],
}


def motion_for_emotion(emotion: str, rng) -> str:
    """Pick a camera move suited to a scene's emotion (deterministic via rng).
    Falls back to the full motion set when the emotion is unknown."""
    e = (emotion or "").lower()
    for key, candidates in _EMOTION_MOTIONS.items():
        if key in e:
            return rng.choice(candidates)
    return rng.choice(SCENE_MOTIONS)


# ── Scene-to-scene transitions (editing techniques) ───────────────────────────
# xfade transition per scene pair, chosen by the INCOMING scene's emotion so the
# cut matches the story beat (calm→gentle fade, dark→dip to black, energetic→
# lateral slide, dramatic→zoom punch). Deterministic per project via rng.
_EMOTION_TRANSITIONS: dict[str, list[str]] = {
    "calm":        ["fade"],
    "peace":       ["fade"],
    "serene":      ["fade"],
    "hope":        ["fade", "smoothright"],
    "reflect":     ["dissolve", "fadeblack"],
    "sad":         ["fadeblack", "dissolve"],
    "somber":      ["fadeblack"],
    "tense":       ["fadeblack", "zoomin"],
    "fear":        ["fadeblack"],
    "anger":       ["zoomin", "wipeleft"],
    "dark":        ["fadeblack"],
    "dramatic":    ["zoomin", "circleopen"],
    "excite":      ["slideleft", "slideright"],
    "energetic":   ["slideleft", "smoothleft"],
    "joy":         ["smoothright", "slideright"],
    "inspire":     ["smoothup", "fade"],
}
_DEFAULT_TRANSITIONS: list[str] = ["fade", "dissolve", "smoothleft", "smoothright"]


def transition_for_emotion(emotion: str, rng) -> str:
    """Pick an xfade transition suited to the incoming scene's emotion
    (deterministic via rng). Falls back to a tasteful neutral pool."""
    e = (emotion or "").lower()
    for key, candidates in _EMOTION_TRANSITIONS.items():
        if key in e:
            return rng.choice(candidates)
    return rng.choice(_DEFAULT_TRANSITIONS)


# ── Style grade (editing pass) ─────────────────────────────────────────────────
# Optional FFmpeg filter chain applied to the stitched timeline, per style/niche.
# Cinematic gets film grain + a light vignette + gentle contrast; dark niches get
# a vignette. Flat 2D art styles (cartoon/ghibli/stickman) stay clean — grain on
# cel-shaded art reads as compression noise, not mood.
_STYLE_GRADE: dict[str, str] = {
    "cinematic": "vignette=PI/5,noise=alls=6:allf=t+u,eq=contrast=1.04:saturation=1.06",
}
_DARK_NICHE_GRADE = "vignette=PI/6,eq=contrast=1.03"


def grade_filter(style_id: str, niche: str = "") -> str:
    """FFmpeg filter chain for the final look ("" = no grade)."""
    grade = _STYLE_GRADE.get(style_id or "")
    if grade:
        return grade
    if "dark" in (niche or "").lower():
        return _DARK_NICHE_GRADE
    return ""

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

# ── Visual styles (the four Faith specified — 2026-07 style prompt PDF) ───────
# `prompt` is Faith's STYLE BLOCK, used exactly as written (her spec: embed it at
# the END of every scene image prompt). `negative` condenses her NEGATIVE STYLE
# list — only the Stability fallback consumes it (Gemini takes no negative
# prompt). `sdxl_preset` is the closest SDXL style_preset for that fallback.
STYLES: dict[str, dict] = {
    "stickman": {
        "label": "Stickman (Modern Explainer Style)",
        "sdxl_preset": "line-art",
        "prompt": ("modern stickman style, clean thin lines, minimalist character design, "
                   "simple circular head and line body, expressive poses through body language, "
                   "flat colors, limited but clear environment elements, subtle shadows, simple "
                   "composition, high readability, smooth digital rendering, consistent line "
                   "thickness, minimalistic aesthetic"),
        "negative": ("realistic human, detailed anatomy, 3D render, photorealism, anime, "
                     "comic book style, messy sketch lines, blank empty background, bright "
                     "saturated colors, thick outlines, exaggerated cartoon proportions"),
    },
    # Faith's written block asked for a "stylized 3D animation look"; her reference
    # image and her 2026-07-24 instruction ("stick to 2D animated cartoon") say flat
    # 2D. The reference wins — this block is her wording retargeted to 2D.
    "cartoon": {
        "label": "Cartoon (Animated Movie Style)",
        "sdxl_preset": "comic-book",
        "prompt": ("high-quality 2D animated cartoon style, flat cel-shaded illustration with "
                   "clean bold outlines, hand-drawn animation feel, expressive facial features, "
                   "slightly exaggerated but believable proportions, vibrant but controlled "
                   "color palette, smooth flat color fills with soft cel shading, detailed 2D "
                   "painted background art, cinematic lighting, subtle depth of field, "
                   "consistent character design, no photorealism, no 3D render"),
        "negative": ("3D render, CGI, photorealism, realistic textures, whiteboard style, "
                     "doodle, stickman, sketch, rough lines, outline-only art, low detail, "
                     "childish drawing, black and white line art, minimal shading, crude "
                     "illustration"),
    },
    "ghibli": {
        "label": "Ghibli Anime (Soft Cinematic Style)",
        "sdxl_preset": "anime",
        "prompt": ("Studio Ghibli inspired style, soft painterly textures, hand-painted "
                   "aesthetic, warm natural lighting, lush detailed environments, gentle color "
                   "palette, expressive but subtle facial features, organic character movement "
                   "feel, slightly whimsical atmosphere, soft shading, atmospheric depth, "
                   "cinematic composition, traditional animation feel, highly detailed "
                   "background art"),
        "negative": ("3D render, hyperrealistic photo, photorealism, CGI, western cartoon, "
                     "whiteboard, doodle, stickman, comic book style, chibi, vector art, flat "
                     "design, oversaturated colors, harsh lighting, low detail, blurry, noisy"),
    },
    "cinematic": {
        "label": "Cinematic",
        "sdxl_preset": "cinematic",
        "prompt": ("cinematic realistic style, ultra-detailed, film-grade lighting, realistic "
                   "textures, natural skin tones, high dynamic range, dramatic shadows, depth "
                   "of field, lens blur, cinematic color grading, volumetric lighting, realistic "
                   "environment detail, sharp focus on subject, professional film still quality"),
        "negative": ("cartoon, anime, illustration, drawing, sketch, painting, low detail, "
                     "flat lighting, oversaturated colors, deformed anatomy, blurry"),
    },
}
STYLE_IDS: list[str] = list(STYLES.keys())
DEFAULT_STYLE = "cartoon"


def style_prompt(style_id: str) -> str:
    """Faith's style block, embedded at the end of every image prompt ("" if unknown)."""
    return (STYLES.get(style_id) or {}).get("prompt", "")


def style_negative(style_id: str) -> str | None:
    """Per-style negative prompt for the Stability fallback engine."""
    return (STYLES.get(style_id) or {}).get("negative")


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
