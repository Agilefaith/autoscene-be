"""Shared FFmpeg subtitle + audio-extract helpers.

Extracted from the retired legacy media worker so the AutoScene assembly stage can
reuse the exact same subtitle styling and Whisper-safe audio extraction. Pure,
stateless helpers — no Celery, no pipeline coupling.
"""

import os
import shutil
import subprocess

from app.schemas.common import RENDER_DIMENSIONS

# Final subtitle-burn encode. veryfast keeps long videos fast; crf 20 stays HD.
X264_QUALITY = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p"]

# Group word-level timestamps into short, readable caption phrases.
_MAX_WORDS_PER_CUE = 6
_MAX_SECONDS_PER_CUE = 2.5
# Only break at a comma/dash once the cue already carries enough words, or a
# short clause would flash on screen on its own.
_MIN_WORDS_BEFORE_CLAUSE_BREAK = 3


def _secs_to_srt(secs: float) -> str:
    h = int(secs // 3600)
    m = int((secs % 3600) // 60)
    s = int(secs % 60)
    ms = int((secs % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _hex_to_ass(color: str) -> str:
    """#RRGGBB → ASS &HAABBGGRR (opaque, BGR byte order)."""
    h = (color or "#FFFFFF").lstrip("#")
    if len(h) != 6:
        h = "FFFFFF"
    rr, gg, bb = h[0:2], h[2:4], h[4:6]
    return f"&H00{bb}{gg}{rr}"


def _group_words(words: list[dict]) -> list[list[dict]]:
    """Chunk word items into short caption phrases.

    A cue always ends at a sentence end, so a caption never runs across a full
    stop into the next sentence ("meet One winter a" — Faith, 2026-08-03; this
    relies on whisper._restore_punctuation putting the punctuation back on the
    word timings). Within a sentence a cue is closed at a clause break (comma,
    dash, colon) when it is already long enough, so the split lands somewhere a
    reader expects rather than mid-phrase.
    """
    groups: list[list[dict]] = []
    cur: list[dict] = []
    for w in words:
        cur.append(w)
        text = str(w.get("word", "")).strip()
        span = (w.get("end", 0) or 0) - (cur[0].get("start", 0) or 0)
        ends_sentence = text[-1:] in ".!?…"
        ends_clause = text[-1:] in ",;:—–"
        full = len(cur) >= _MAX_WORDS_PER_CUE or span >= _MAX_SECONDS_PER_CUE
        if ends_sentence or full or (ends_clause and len(cur) >= _MIN_WORDS_BEFORE_CLAUSE_BREAK):
            groups.append(cur)
            cur = []
    if cur:
        groups.append(cur)
    return groups


def _burn_subtitles(input_path: str, output_path: str, words: list[dict], style: dict) -> bool:
    """Burn readable phrase-level subtitles into the video.

    Short phrases (not one word at a time), a strong black outline + shadow so the
    text is readable on ANY background, and the user's chosen fill color. Returns
    True on success; on failure it logs the real ffmpeg error and ships the video
    without captions (never a silent, untraceable drop)."""
    if not words:
        shutil.copy(input_path, output_path)
        return False
    srt_path = input_path + ".srt"
    try:
        srt_lines = []
        for i, g in enumerate(_group_words(words)):
            start = _secs_to_srt(g[0]["start"])
            end = _secs_to_srt(g[-1]["end"])
            text = " ".join(str(x.get("word", "")).strip() for x in g).strip()
            srt_lines.append(f"{i+1}\n{start} --> {end}\n{text}\n")
        with open(srt_path, "w") as f:
            f.write("\n".join(srt_lines))

        primary = _hex_to_ass(style.get("font_color", "#FFFFFF"))
        # Font size is in the render-canvas coordinate space (PlayResY below), so a
        # readable caption is ~4–6% of height. Default matches SUBTITLE_SIZES "L".
        size = style.get("font_size") or 84
        placement = style.get("placement", "bottom")
        alignment = 2 if placement == "bottom" else (5 if placement == "center" else 8)
        font_style = style.get("font_style", "bold")
        # Map to font families that actually ship in the image (fonts-liberation):
        # "Georgia"/"Courier New" are not bundled, so libass silently substituted a
        # default. Liberation Serif/Mono resolve reliably; Arial aliases to Liberation Sans.
        font_name = {"serif": "Liberation Serif", "mono": "Liberation Mono"}.get(font_style, "Arial")
        bold = 1 if font_style == "bold" else 0
        italic = 1 if font_style == "italic" else 0
        # Anchor FontSize to the real output canvas — without PlayResX/PlayResY,
        # libass falls back to its own default script resolution and stretches it
        # to fill the frame, inflating the rendered text far beyond `size`.
        play_w, play_h = RENDER_DIMENSIONS.get(style.get("format", "9:16"), (1080, 1920))

        # BorderStyle=1 (outline + drop shadow), thick black outline → readable anywhere.
        force_style = (
            f"FontName={font_name},FontSize={size},PrimaryColour={primary},"
            f"OutlineColour=&H00000000,BorderStyle=1,Outline=2,Shadow=1,"
            f"Alignment={alignment},MarginV=40,Bold={bold},Italic={italic},"
            f"PlayResX={play_w},PlayResY={play_h}"
        )
        vf = f"subtitles={srt_path}:force_style='{force_style}'"

        subprocess.run(
            ["ffmpeg", "-y", "-i", input_path, "-vf", vf, "-c:a", "copy", *X264_QUALITY, output_path],
            check=True, capture_output=True, timeout=1200,
        )
        return True
    except (FileNotFoundError, subprocess.SubprocessError) as e:
        stderr = getattr(e, "stderr", b"")
        detail = stderr.decode(errors="ignore")[-600:] if isinstance(stderr, (bytes, bytearray)) else str(e)
        print(f"[subtitles] burn failed, shipping without captions: {detail}")
        shutil.copy(input_path, output_path)
        return False
    finally:
        if os.path.exists(srt_path):
            os.remove(srt_path)


def _extract_audio(video_path: str, audio_path: str) -> bool:
    """Extract a small mono MP3 for transcription.

    Whisper rejects files over 25 MB, and a 1080p video easily exceeds that, so we
    never send the full video — a 16 kHz mono 64 kbps MP3 is tiny (~0.5 MB/min) and
    keeps us well under the limit. Returns False if extraction fails (caller falls
    back to the video).
    """
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", video_path, "-vn", "-ac", "1", "-ar", "16000",
             "-b:a", "64k", audio_path],
            check=True, capture_output=True, timeout=300,
        )
        return os.path.exists(audio_path) and os.path.getsize(audio_path) > 0
    except (FileNotFoundError, subprocess.SubprocessError):
        return False
