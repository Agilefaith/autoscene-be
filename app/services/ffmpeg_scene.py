"""FFmpeg motion engine — AutoScene Mode 1 & Mode 2 (PRD §4.3 / §4.4).

Turns still SDXL images into moving scene clips:
  • Mode 1 — one image per scene → Ken Burns (zoom/pan) clip.
  • Mode 2 — three images per scene → per-image motion + crossfade A→B→C.

Motion expressions are written so they stay within bounds without min()/max(), which
keeps the filtergraph comma-free (no escaping) and the motion perfectly smooth.
Images are upscaled 2× before zoompan to eliminate the classic integer-step jitter.
"""

import os
import subprocess
import tempfile

# Final-quality encode (used where output is the deliverable master).
X264_QUALITY = ["-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p"]
# Intermediate scene clips + crossfades: `veryfast` is far quicker and the quality
# difference is invisible under motion (these get re-encoded again downstream).
SCENE_ENCODE = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p"]

# Motion magnitudes — gentle, cinematic (PRD: "no aggressive or fast zooms").
_ZOOM_DELTA = 0.18      # total zoom travel for zoom_in / zoom_out
_PAN_ZOOM = 1.12        # constant zoom that gives pans room to move
_ZOOMPAN_ZOOM = 0.12    # zoom travel for the combined zoom+pan motion
_UPSCALE = 2            # working-resolution multiplier for smooth motion


def _frames(duration: float, fps: int) -> int:
    return max(1, round(duration * fps))


def _motion_filter(motion: str, w: int, h: int, duration: float, fps: int) -> str:
    """Build the scale→crop→(rotate→)zoompan filtergraph for one still image."""
    n = _frames(duration, fps)
    wu, hu = w * _UPSCALE, h * _UPSCALE
    pre = ""  # optional filter stage between the upscale and zoompan

    # progress p = on/n ∈ [0,1]; all expressions stay in-bounds (no min/max needed).
    if motion == "zoom_in":
        z = f"1+{_ZOOM_DELTA}*on/{n}"
        x = "iw/2-(iw/zoom/2)"
        y = "ih/2-(ih/zoom/2)"
    elif motion == "zoom_out":
        z = f"{1 + _ZOOM_DELTA}-{_ZOOM_DELTA}*on/{n}"
        x = "iw/2-(iw/zoom/2)"
        y = "ih/2-(ih/zoom/2)"
    elif motion == "pan_right":
        z = f"{_PAN_ZOOM}"
        x = f"(iw-iw/zoom)*on/{n}"
        y = "ih/2-(ih/zoom/2)"
    elif motion == "pan_left":
        z = f"{_PAN_ZOOM}"
        x = f"(iw-iw/zoom)*(1-on/{n})"
        y = "ih/2-(ih/zoom/2)"
    elif motion == "pan_up":
        z = f"{_PAN_ZOOM}"
        x = "iw/2-(iw/zoom/2)"
        y = f"(ih-ih/zoom)*(1-on/{n})"
    elif motion == "pan_down":
        z = f"{_PAN_ZOOM}"
        x = "iw/2-(iw/zoom/2)"
        y = f"(ih-ih/zoom)*on/{n}"
    elif motion == "zoom_in_tl":  # diagonal push into the top-left
        z = f"1+{_ZOOM_DELTA}*on/{n}"
        x = "0"
        y = "0"
    elif motion == "zoom_in_br":  # diagonal push into the bottom-right
        z = f"1+{_ZOOM_DELTA}*on/{n}"
        x = "iw-iw/zoom"
        y = "ih-ih/zoom"
    elif motion == "zoom_out_tl":  # diagonal pull-back from the top-left
        z = f"{1 + _ZOOM_DELTA}-{_ZOOM_DELTA}*on/{n}"
        x = "0"
        y = "0"
    elif motion == "zoom_out_br":  # diagonal pull-back from the bottom-right
        z = f"{1 + _ZOOM_DELTA}-{_ZOOM_DELTA}*on/{n}"
        x = "iw-iw/zoom"
        y = "ih-ih/zoom"
    elif motion == "slow_drift":  # documentary micro-drift: near-still with a slow lateral glide
        z = "1.08"
        x = f"(iw-iw/zoom)*(0.25+0.5*on/{n})"
        y = "ih/2-(ih/zoom/2)"
    elif motion == "crane_up":    # crane: pull back while the frame rises
        z = f"{1 + _ZOOM_DELTA}-{_ZOOM_DELTA}*on/{n}"
        x = "iw/2-(iw/zoom/2)"
        y = f"(ih-ih/zoom)*(1-on/{n})"
    elif motion == "pulse_in":    # breathing push: eases in and settles back (half sine)
        z = f"1+0.1*sin(PI*on/{n})"
        x = "iw/2-(iw/zoom/2)"
        y = "ih/2-(ih/zoom/2)"
    elif motion == "rotate_zoom":  # subtle handheld rotation under a constant zoom
        # ~0.26°/s — at the working zoom of 1.12 the crop window stays inside the
        # rotated frame for small angles, so no black corners appear.
        pre = "rotate=a='0.0045*t':c=black@0,"
        z = f"{_PAN_ZOOM}"
        x = "iw/2-(iw/zoom/2)"
        y = "ih/2-(ih/zoom/2)"
    else:  # zoom_pan (default / combined)
        z = f"1+{_ZOOMPAN_ZOOM}*on/{n}"
        x = f"(iw-iw/zoom)*on/{n}"
        y = "ih/2-(ih/zoom/2)"

    return (
        f"scale={w}:{h}:force_original_aspect_ratio=increase,"
        f"crop={w}:{h},"
        f"scale={wu}:{hu},"
        f"{pre}"
        f"zoompan=z='{z}':x='{x}':y='{y}':d={n}:s={w}x{h}:fps={fps},"
        f"format=yuv420p,setsar=1"
    )


def _render_still_clip(image_path: str, out_path: str, w: int, h: int,
                       duration: float, motion: str, fps: int) -> None:
    """Render a single still image into a moving clip. Raises on failure."""
    vf = _motion_filter(motion, w, h, duration, fps)
    subprocess.run(
        ["ffmpeg", "-y", "-loop", "1", "-i", image_path, "-t", f"{duration:.3f}",
         "-vf", vf, "-r", str(fps), *SCENE_ENCODE, out_path],
        check=True, capture_output=True, timeout=600,
    )


def render_scene_clip(
    image_paths: list[str],
    out_path: str,
    *,
    width: int,
    height: int,
    duration: float,
    motions: list[str],
    fps: int = 30,
    crossfade: float = 0.6,
) -> None:
    """Render one scene's clip.

    Mode 1 → pass a single image. Mode 2 → pass three images; they are rendered with
    individual motion and crossfaded A→B→C so the total clip length stays `duration`.
    Raises CalledProcessError if FFmpeg fails (the caller retries the scene).
    """
    if not image_paths:
        raise ValueError("render_scene_clip requires at least one image")

    # Mode 1 — single still.
    if len(image_paths) == 1:
        _render_still_clip(image_paths[0], out_path, width, height, duration,
                           motions[0] if motions else "zoom_in", fps)
        return

    # Mode 2 — three stills crossfaded. Each segment is sized so the final length
    # (sum − 2×crossfade) equals `duration`.
    imgs = image_paths[:3]
    while len(motions) < len(imgs):
        motions = motions + ["zoom_in"]
    cf = max(0.1, min(crossfade, duration / 4))
    seg = (duration + 2 * cf) / 3

    tmpdir = tempfile.mkdtemp()
    try:
        seg_clips: list[str] = []
        for i, img in enumerate(imgs):
            seg_path = os.path.join(tmpdir, f"seg_{i}.mp4")
            _render_still_clip(img, seg_path, width, height, seg, motions[i], fps)
            seg_clips.append(seg_path)

        off_a = seg - cf
        off_b = 2 * seg - 2 * cf
        filtergraph = (
            f"[0][1]xfade=transition=fade:duration={cf:.3f}:offset={off_a:.3f}[ab];"
            f"[ab][2]xfade=transition=fade:duration={cf:.3f}:offset={off_b:.3f}"
        )
        subprocess.run(
            ["ffmpeg", "-y",
             "-i", seg_clips[0], "-i", seg_clips[1], "-i", seg_clips[2],
             "-filter_complex", filtergraph, "-r", str(fps), *SCENE_ENCODE, out_path],
            check=True, capture_output=True, timeout=900,
        )
    finally:
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)


def concat_scene_clips(clip_paths: list[str], out_path: str,
                       *, transitions: list[str] | None = None,
                       trans_seconds: float = 0.4, grade: str = "") -> None:
    """Concatenate rendered scene clips into one silent video.

    Hard cuts by default (concat demuxer — fast, exact length). When `transitions`
    is set (one xfade type per scene PAIR, so len == n_clips - 1; a single-element
    list is broadcast), consecutive scenes are joined with xfades of
    `trans_seconds`. Clips must have been rendered `trans_seconds` longer per
    scene (see scene_render) so the crossfades consume the extra tail and the
    total timeline stays synced.

    `grade` is an optional FFmpeg filter chain (vignette/grain/eq — see
    schemas.common.grade_filter) applied to the stitched timeline. It rides the
    xfade encode for free, so it only applies on the transition path — the
    hard-cut path is a stream copy and stays untouched (long videos skip
    transitions for speed anyway).
    """
    if not clip_paths:
        raise ValueError("concat_scene_clips requires at least one clip")

    if transitions and len(clip_paths) > 1:
        pairs = len(clip_paths) - 1
        tlist = (transitions * pairs)[:pairs] if len(transitions) < pairs else transitions[:pairs]
        _concat_with_xfade(clip_paths, out_path, tlist, trans_seconds, grade)
        return

    tmpdir = tempfile.mkdtemp()
    try:
        list_path = os.path.join(tmpdir, "concat.txt")
        with open(list_path, "w") as f:
            for p in clip_paths:
                f.write(f"file '{os.path.abspath(p)}'\n")
        # Stream-copy (no re-encode): all scene clips are encoded identically, so
        # this joins them in seconds instead of re-encoding the whole timeline.
        subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_path,
             "-c", "copy", out_path],
            check=True, capture_output=True, timeout=900,
        )
    finally:
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)


def _concat_with_xfade(clip_paths: list[str], out_path: str,
                       transitions: list[str], t: float, grade: str = "") -> None:
    """Chain xfade transitions (one type per pair) across all clips in one
    encode, optionally finishing with a style-grade filter chain."""
    durations = [probe_duration(p) for p in clip_paths]
    inputs: list[str] = []
    for p in clip_paths:
        inputs += ["-i", p]

    # Build the xfade chain: each xfade overlaps the running timeline with the
    # next clip by `t`, so offset_k = (sum of prior clip lengths) - k*t.
    chain: list[str] = []
    prev = "[0:v]"
    cum = durations[0]
    last = len(clip_paths) - 1
    for i in range(1, len(clip_paths)):
        off = max(0.0, cum - t)
        out_label = f"[v{i}]" if i < last else "[vout]"
        trans = transitions[i - 1] if i - 1 < len(transitions) else "fade"
        chain.append(
            f"{prev}[{i}:v]xfade=transition={trans}:duration={t:.3f}:offset={off:.3f}{out_label}"
        )
        prev = out_label
        cum += durations[i] - t

    map_label = "[vout]"
    if grade:
        chain.append(f"[vout]{grade}[vfinal]")
        map_label = "[vfinal]"

    subprocess.run(
        ["ffmpeg", "-y", *inputs,
         "-filter_complex", ";".join(chain), "-map", map_label,
         *SCENE_ENCODE, out_path],
        check=True, capture_output=True, timeout=1800,
    )


def mux_audio(video_path: str, audio_path: str, out_path: str) -> None:
    """Mux a voiceover track onto the stitched video.

    The video is the visual timeline; `-shortest` trims to whichever ends first so a
    slightly-long audio tail or video tail never leaves a frozen/black gap.
    """
    subprocess.run(
        ["ffmpeg", "-y", "-i", video_path, "-i", audio_path,
         "-map", "0:v:0", "-map", "1:a:0",
         "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", out_path],
        check=True, capture_output=True, timeout=600,
    )


def probe_duration(path: str) -> float:
    """Return media duration in seconds (0.0 if unprobeable)."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", path],
            capture_output=True, text=True, timeout=30,
        ).stdout.strip()
        return float(out)
    except (ValueError, FileNotFoundError, subprocess.SubprocessError):
        return 0.0
