"""Shared FFmpeg subtitle + audio-extract helpers.

Extracted from the retired legacy media worker so the AutoScene assembly stage can
reuse the exact same subtitle styling and Whisper-safe audio extraction. Pure,
stateless helpers — no Celery, no pipeline coupling.
"""

import os
import shutil
import subprocess

# x264 encode settings for every re-encode pass. Without an explicit CRF, ffmpeg
# defaults to CRF 23 (~750 kbps, blocky); CRF 18 keeps the result visually HD.
# yuv420p ensures broad player/browser compatibility.
X264_QUALITY = ["-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p"]


def _secs_to_srt(secs: float) -> str:
    h = int(secs // 3600)
    m = int((secs % 3600) // 60)
    s = int(secs % 60)
    ms = int((secs % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _burn_subtitles(input_path: str, output_path: str, words: list[dict], style: dict) -> bool:
    """Burn word-level subtitles into video using FFmpeg.

    Returns True on success. Falls back gracefully (plain copy) if FFmpeg is
    unavailable or built without libass, reporting failure to the caller.
    """
    try:
        # Build SRT from word timestamps
        srt_lines = []
        for i, w in enumerate(words):
            start = _secs_to_srt(w["start"])
            end = _secs_to_srt(w["end"])
            srt_lines.append(f"{i+1}\n{start} --> {end}\n{w['word']}\n")
        srt_content = "\n".join(srt_lines)

        srt_path = input_path + ".srt"
        with open(srt_path, "w") as f:
            f.write(srt_content)

        color = style.get("font_color", "#FFFFFF").replace("#", "&H00") + "&"
        size = style.get("font_size", 24)
        placement = style.get("placement", "bottom")
        alignment = 2 if placement == "bottom" else (5 if placement == "center" else 8)

        font_style = style.get("font_style", "bold")
        font_name_map = {
            "sans":   "Arial",
            "serif":  "Georgia",
            "mono":   "Courier New",
            "bold":   "Arial",
            "italic": "Arial",
        }
        font_name = font_name_map.get(font_style, "Arial")
        bold   = 1 if font_style in ("bold",) else 0
        italic = 1 if font_style == "italic" else 0

        vf = (
            f"subtitles={srt_path}:force_style='"
            f"FontName={font_name},FontSize={size},PrimaryColour={color},"
            f"Alignment={alignment},Bold={bold},Italic={italic}'"
        )

        subprocess.run(
            ["ffmpeg", "-y", "-i", input_path, "-vf", vf, "-c:a", "copy", *X264_QUALITY, output_path],
            check=True, capture_output=True, timeout=600,
        )
        os.remove(srt_path)
        return True
    except (FileNotFoundError, subprocess.SubprocessError):
        # subtitles filter unavailable (ffmpeg built without libass) or failed —
        # copy through so the video still ships, but report failure to the caller.
        shutil.copy(input_path, output_path)
        return False


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
