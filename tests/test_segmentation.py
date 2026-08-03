"""Deterministic scene segmentation (services/scene_engine.py).

Faith reported single images sitting on screen for 15-26s despite a 10s setting.
The cause was that scene boundaries were decided by the model, which returned
fewer scenes than requested; the voiceover stage then spread the real narration
across those few scenes. Boundaries are now computed here, so both the scene count
and the narration each scene carries are exact.
"""

from app.core.config import get_settings
from app.services.scene_engine import (
    segment_script, split_by_duration, split_into_stanzas, uses_stanza_split,
)

settings = get_settings()

STANZA_SCRIPT = (
    "Your brain is lying to you.\nAnd you believe it every day.\n\n"
    "You think you're not good enough.\nThat's a story. Not a fact.\n\n"
    "You think people are judging you.\nMost of them aren't even thinking about you."
)

PROSE = (
    "The village sat at the edge of the desert. Every morning before sunrise she carried "
    "water from the well. She had little but shared her bread with every traveller who "
    "passed. One evening a stranger came, weak and hungry, and she gave him her last loaf. "
    "By morning her jar of flour had not run empty. Kindness is never truly spent."
)


def test_stanza_niches_are_recognised_both_spellings():
    for n in ("Psychology", "psychology & human behaviour",
              "Psychology & Human Behavior", "Relatable Life Storytelling"):
        assert uses_stanza_split(n), n
    for n in ("Bible storytelling", "History", "", "Finance storytelling"):
        assert not uses_stanza_split(n), n


def test_stanza_split_gives_one_scene_per_stanza():
    units = segment_script(STANZA_SCRIPT, niche="Psychology", scene_seconds=10)
    assert len(units) == 3
    assert units[0].startswith("Your brain is lying")
    assert "not good enough" in units[1]


def test_stanza_split_falls_back_to_line_pairs_without_blank_lines():
    script = "Line one.\nLine two.\nLine three.\nLine four."
    assert len(split_into_stanzas(script)) == 2


def test_duration_split_keeps_every_scene_within_its_budget():
    scene_seconds = 10
    units = split_by_duration(PROSE, scene_seconds)
    budget = round(scene_seconds * settings.script_words_per_minute / 60)
    assert len(units) > 1
    for u in units:
        # a unit may exceed the budget only when it is a single long sentence
        assert len(u.split()) <= budget or len(u.split(".")) <= 2, u


def test_every_word_of_the_script_survives_segmentation():
    for script, niche in ((PROSE, "History"), (STANZA_SCRIPT, "Psychology")):
        joined = " ".join(segment_script(script, niche=niche, scene_seconds=10)).split()
        assert joined == script.split()


def test_segmentation_respects_the_scene_cap():
    long_script = " ".join(f"Sentence number {i} here." for i in range(2000))
    units = segment_script(long_script, niche="History", scene_seconds=10)
    assert len(units) <= settings.scene_max_count
