"""Deterministic scene segmentation (services/scene_engine.py).

Faith reported single images sitting on screen for 15-26s despite a 10s setting.
The cause was that scene boundaries were decided by the model, which returned
fewer scenes than requested; the voiceover stage then spread the real narration
across those few scenes. Boundaries are now computed here, so both the scene count
and the narration each scene carries are exact.
"""

import pytest

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

# A custom-pasted script formatted as ordinary paragraphs (3 sentences, blank-line
# separated) rather than the 2-line stanza shape the AI generator writes. Faith
# hit this exact shape (2026-08-11): each paragraph came back as ONE scene
# holding every sentence in it, instead of the ~2-sentence stanza the niche
# promises.
PROSE_PARAGRAPHS_SCRIPT = (
    "I used to think loving someone meant staying no matter what. I held onto every "
    "excuse he gave me. I told myself things would get better if I just tried harder.\n\n"
    "But every promise he made turned into another disappointment. My friends kept "
    "telling me I deserved better, but I didn't want to hear it.\n\n"
    "Then one morning I woke up and realized I didn't recognize myself anymore. That "
    "was the moment everything changed."
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


def test_stanza_split_caps_ordinary_paragraphs_at_two_sentences():
    """A pasted script with normal paragraphing (3 sentences/blank-line block, no
    internal line breaks) must still come out in ~2-sentence units, not one giant
    scene per paragraph — this is the exact regression Faith reported."""
    units = segment_script(PROSE_PARAGRAPHS_SCRIPT, niche="Relatable Life Storytelling",
                           scene_seconds=10)
    assert len(units) > 3, "one unit per paragraph means the cap isn't being applied"
    for u in units:
        n_sentences = sum(u.count(c) for c in ".!?")
        assert n_sentences <= 2, f"unit has more than 2 sentences: {u!r}"


def test_stanza_split_still_respects_paragraph_boundaries():
    """Pairing must not merge the tail of one paragraph into the next."""
    units = split_into_stanzas(PROSE_PARAGRAPHS_SCRIPT)
    assert not any("harder." in u and "But every promise" in u for u in units)


def test_stanza_split_leaves_an_already_two_sentence_paragraph_alone():
    """A paragraph that is already the target shape must not be needlessly
    re-split into two units of one sentence each."""
    script = "The sky went dark early. Nobody said a word.\n\nThen the rain started. She smiled anyway."
    units = split_into_stanzas(script)
    assert units == ["The sky went dark early. Nobody said a word.",
                     "Then the rain started. She smiled anyway."]


def test_stanza_split_handles_a_single_sentence_paragraph():
    script = "She finally let go.\n\nEverything else followed."
    assert split_into_stanzas(script) == ["She finally let go.", "Everything else followed."]


def test_stanza_split_handles_mixed_paragraph_shapes_in_one_script():
    """One script can mix an already-2-line paragraph with an ordinary-prose one;
    each block is capped independently of how its neighbors are shaped."""
    script = (
        "Two line stanza here.\nSecond line of it.\n\n"
        "But this next paragraph runs long. It has three full sentences. "
        "None of them should share a scene with all the others."
    )
    units = split_into_stanzas(script)
    assert units[0] == "Two line stanza here. Second line of it."
    assert all(sum(u.count(c) for c in ".!?") <= 2 for u in units[1:])
    assert len(units) == 3  # stanza(1) + prose(2, capped at 2 sentences each)


def test_stanza_split_tolerates_extra_blank_lines_between_paragraphs():
    script = "First paragraph, one line.\n\n\n\nSecond paragraph, one line."
    assert split_into_stanzas(script) == ["First paragraph, one line.", "Second paragraph, one line."]


def test_stanza_split_on_an_empty_or_whitespace_script_does_not_crash():
    assert split_into_stanzas("") == [""]
    assert split_into_stanzas("   \n\n   ") == [""]


def test_stanza_niche_is_isolated_from_other_niches():
    """The same prose script must NOT be capped at 2 sentences under a
    non-stanza niche — that path uses split_by_duration instead."""
    stanza_units = segment_script(PROSE_PARAGRAPHS_SCRIPT, niche="Relatable Life Storytelling",
                                  scene_seconds=10)
    duration_units = segment_script(PROSE_PARAGRAPHS_SCRIPT, niche="History", scene_seconds=10)
    assert len(stanza_units) != len(duration_units) or stanza_units != duration_units
    assert all(sum(u.count(c) for c in ".!?") <= 2 for u in stanza_units)


def test_every_word_survives_for_the_paragraph_shaped_script_too():
    """Extends the existing word-preservation guarantee to the exact shape that
    was broken: ordinary paragraphs under a stanza niche."""
    joined = " ".join(segment_script(PROSE_PARAGRAPHS_SCRIPT, niche="Relatable Life Storytelling",
                                     scene_seconds=10)).split()
    assert joined == PROSE_PARAGRAPHS_SCRIPT.split()


@pytest.mark.parametrize("n_sentences_per_paragraph", [1, 2, 3, 4, 5, 6])
def test_stanza_split_caps_paragraphs_of_varying_length(n_sentences_per_paragraph):
    """Sweep paragraph lengths from 1 to 6 sentences — every resulting unit must
    still hold at most 2 sentences, regardless of how long the source paragraph is."""
    paragraph = " ".join(f"Sentence {i}." for i in range(1, n_sentences_per_paragraph + 1))
    script = f"{paragraph}\n\n{paragraph}"
    units = split_into_stanzas(script)
    for u in units:
        assert sum(u.count(c) for c in ".!?") <= 2, u
    # word count must be preserved exactly
    assert " ".join(units).split() == script.replace("\n\n", " ").split()


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
