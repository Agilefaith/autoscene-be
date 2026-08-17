"""Scene duration from real speech timing, not a constant-speaking-rate guess.

Faith reported images running ahead of the narration on her last 2 videos
(2026-08-16). The word-count-proportional estimate assumed every word takes the
same time to speak; real narration doesn't, so the error compounded across a
long video. These durations are read directly off Whisper's word timestamps
instead, so each scene boundary lands where the words were actually spoken.
"""

from app.services.whisper import scene_durations_from_transcript, _scene_start_times


def _segments(text: str, wps: float = 2.0, start: float = 0.0) -> list[dict]:
    """Fabricate word-level timestamps for a string, `wps` words per second."""
    out = []
    t = start
    for w in text.split():
        out.append({"word": w, "start": round(t, 2), "end": round(t + 1 / wps, 2)})
        t += 1 / wps
    return out


def test_boundaries_land_exactly_on_the_words_actually_spoken():
    scenes = [{"scene_text": "The lighthouse stood dark."},
             {"scene_text": "Then the lamp lit."}]
    segments = _segments("The lighthouse stood dark. Then the lamp lit.", wps=2.0)
    starts = _scene_start_times(scenes, segments)
    assert starts[0] == 0.0
    assert starts[1] == segments[4]["start"]  # first word of scene 2 ("Then")


def test_durations_have_zero_drift_regardless_of_word_count_per_scene():
    """The old estimate compounded error across many scenes; this must not,
    even when scenes vary wildly in word count."""
    scenes = [{"scene_text": "Hi."}, {"scene_text": "A much longer scene with many more words in it."},
             {"scene_text": "Short again."}]
    text = " ".join(s["scene_text"] for s in scenes)
    segments = _segments(text, wps=3.0)
    audio_seconds = segments[-1]["end"]
    # min_scene_seconds=0 isolates the tiling property from the (separately
    # tested) floor — a genuinely 0.3s scene is a floor question, not a drift one.
    durations = scene_durations_from_transcript(scenes, segments, audio_seconds,
                                                 min_scene_seconds=0.0)
    # boundaries must tile the whole audio with no gap and no overlap
    starts = _scene_start_times(scenes, segments)
    for i in range(len(scenes) - 1):
        assert round(starts[i] + durations[i], 2) == starts[i + 1]
    assert round(starts[-1] + durations[-1], 2) == round(audio_seconds, 2)


def test_a_slower_scene_gets_more_time_than_a_faster_one_of_equal_word_count():
    """This is the actual bug: word-count-proportional gives two 4-word scenes
    the same duration even when one is spoken twice as fast as the other."""
    scenes = [{"scene_text": "One two three four"}, {"scene_text": "Five six seven eight"}]
    fast = _segments("One two three four", wps=4.0)
    slow_start = fast[-1]["end"]
    slow = _segments("Five six seven eight", wps=1.0, start=slow_start)
    segments = fast + slow
    audio_seconds = segments[-1]["end"]
    durations = scene_durations_from_transcript(scenes, segments, audio_seconds,
                                                 min_scene_seconds=0.0)
    assert durations[1] > durations[0] * 2  # the slow scene really did take longer


def test_min_scene_seconds_floor_still_applies():
    scenes = [{"scene_text": "Hi."}, {"scene_text": "Bye."}]
    segments = _segments("Hi. Bye.", wps=10.0)  # both words spoken in a fraction of a second
    durations = scene_durations_from_transcript(scenes, segments, segments[-1]["end"],
                                                 min_scene_seconds=2.0)
    assert all(d >= 2.0 for d in durations)


def test_tolerates_whisper_dropping_a_word_without_permanently_desyncing():
    """Whisper occasionally mishears/drops a word; one miss must not throw off
    every scene boundary after it (same tolerance as _restore_punctuation)."""
    scenes = [{"scene_text": "The quick brown fox jumps."}, {"scene_text": "Over the lazy dog."}]
    # Whisper dropped "brown" entirely.
    segments = _segments("The quick fox jumps over the lazy dog.", wps=2.0)
    starts = _scene_start_times(scenes, segments)
    # scene 2 ("Over...") should still land near its real word, not drift to the end
    over_word = next(s for s in segments if s["word"] == "over")
    assert abs(starts[1] - over_word["start"]) <= 0.5


def test_an_empty_scene_text_does_not_crash_and_holds_position():
    scenes = [{"scene_text": "Hello there."}, {"scene_text": ""}, {"scene_text": "Goodbye now."}]
    segments = _segments("Hello there. Goodbye now.", wps=2.0)
    starts = _scene_start_times(scenes, segments)
    assert starts[1] == starts[0]  # empty scene doesn't advance or go backward
    assert starts[2] >= starts[1]


def test_no_transcript_words_falls_back_to_zero_without_crashing():
    scenes = [{"scene_text": "Anything at all."}]
    assert _scene_start_times(scenes, []) == [0.0]
    assert scene_durations_from_transcript(scenes, [], 5.0) == [5.0]
