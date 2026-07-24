"""Motion, transition, and grade selection (Faith 2026-07-04: 'wide range of
motion effects, camera angles, and editing techniques')."""

import random

from app.schemas.common import (
    SCENE_MOTIONS, motion_for_emotion, transition_for_emotion, grade_filter,
)
from app.services.ffmpeg_scene import _motion_filter


def test_every_motion_builds_a_zoompan_filtergraph():
    for motion in SCENE_MOTIONS:
        vf = _motion_filter(motion, 1920, 1080, duration=5.0, fps=30)
        assert "zoompan=" in vf, motion
        assert vf.endswith("format=yuv420p,setsar=1"), motion
        # zoompan expressions must stay comma-free (commas would need escaping
        # and break the filtergraph) — check the quoted z/x/y expressions.
        zoompan = vf.split("zoompan=")[1].split(",format")[0]
        for part in zoompan.split(":")[:3]:  # z=, x=, y=
            expr = part.split("=", 1)[1].strip("'")
            assert "," not in expr, f"{motion}: comma in expression {expr!r}"


def test_rotate_zoom_inserts_rotate_stage_before_zoompan():
    vf = _motion_filter("rotate_zoom", 1920, 1080, duration=5.0, fps=30)
    assert "rotate=" in vf
    assert vf.index("rotate=") < vf.index("zoompan=")


def test_unknown_motion_falls_back_to_zoom_pan():
    vf = _motion_filter("nonsense", 1920, 1080, duration=5.0, fps=30)
    assert "zoompan=" in vf


def test_motion_and_transition_selection_are_deterministic():
    a = motion_for_emotion("dramatic", random.Random(7))
    b = motion_for_emotion("dramatic", random.Random(7))
    assert a == b and a in SCENE_MOTIONS

    t1 = transition_for_emotion("energetic", random.Random(7))
    t2 = transition_for_emotion("energetic", random.Random(7))
    assert t1 == t2 and t1 in {"slideleft", "smoothleft"}


def test_transition_unknown_emotion_uses_neutral_pool():
    t = transition_for_emotion("zzz-unknown", random.Random(1))
    assert t in {"fade", "dissolve", "smoothleft", "smoothright"}


def test_grade_filter_by_style_and_niche():
    assert "vignette" in grade_filter("cinematic")
    assert "noise" in grade_filter("cinematic")
    # Flat 2D styles stay clean…
    assert grade_filter("cartoon") == ""
    assert grade_filter("ghibli") == ""
    # …unless the niche is dark, which gets a vignette-only grade.
    assert "vignette" in grade_filter("cartoon", "Dark Truths / Reality")
