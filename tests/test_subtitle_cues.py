"""Caption cue boundaries (services/whisper.py + services/ffmpeg_subs.py).

Whisper's word-level output has no punctuation ("below", not "below."), so the
sentence-end check never fired and captions ran straight through a full stop:
"meet One winter a" and "the tower The villagers who had" (Faith, 2026-08-03).
Punctuation is now mapped back onto the word timings before cues are grouped.
"""

from app.services.ffmpeg_subs import _group_words
from app.services.whisper import _restore_punctuation

TEXT = ("The old lighthouse keeper never spoke to anyone in the village below. "
        "Every night without fail, he climbed the spiral stairs and lit the lamp "
        "for sailors he would never meet. One winter a storm took the roof clean "
        "off the tower.")

BARE_WORDS = [
    {"word": w, "start": i * 0.4, "end": i * 0.4 + 0.35}
    for i, w in enumerate(
        "The old lighthouse keeper never spoke to anyone in the village below "
        "Every night without fail he climbed the spiral stairs and lit the lamp "
        "for sailors he would never meet One winter a storm took the roof clean "
        "off the tower".split()
    )
]


def _cue_texts(words):
    return [" ".join(w["word"] for w in g) for g in _group_words(words)]


def test_punctuation_is_restored_onto_word_timings():
    words = _restore_punctuation(BARE_WORDS, TEXT)
    assert len(words) == len(BARE_WORDS)          # nothing added or dropped
    by_text = {w["word"] for w in words}
    assert "below." in by_text
    assert "meet." in by_text
    assert "fail," in by_text
    # timings must survive untouched
    assert [w["start"] for w in words] == [w["start"] for w in BARE_WORDS]


def test_no_cue_runs_across_a_full_stop():
    cues = _cue_texts(_restore_punctuation(BARE_WORDS, TEXT))
    for cue in cues:
        # a sentence end may only appear as the LAST token of a cue
        inner = cue.split()[:-1]
        assert not any(tok.endswith((".", "!", "?")) for tok in inner), cue


def test_the_reported_bad_cues_no_longer_appear():
    cues = _cue_texts(_restore_punctuation(BARE_WORDS, TEXT))
    joined = " | ".join(cues)
    assert "meet One winter" not in joined
    assert "tower The villagers" not in joined


def test_unpunctuated_input_still_produces_cues():
    # if the two streams ever drift apart we must still caption the video
    cues = _cue_texts(_restore_punctuation(BARE_WORDS, ""))
    assert cues and all(c.strip() for c in cues)
