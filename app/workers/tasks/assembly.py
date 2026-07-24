"""Final assembly stage (AutoScene §4.7 / Definition of Done).

Stitches the rendered scene clips, muxes the voiceover, burns subtitles, and uploads
the final MP4. Reuses the proven subtitle/transcription helpers from the media worker
so styling stays identical across both pipelines.
"""

import asyncio
import os
import shutil
import tempfile
import time
from datetime import datetime, timezone

import httpx

from app.core.celery_app import celery_app
from app.services.supabase import get_supabase_client
from app.services.backblaze import upload_video
from app.services.whisper import transcribe
from app.services import ffmpeg_scene
# Shared subtitle burn + audio-extract helpers (extracted from the retired media worker).
from app.services.ffmpeg_subs import _burn_subtitles, _extract_audio
from app.workers.tasks.project_common import (
    log_event, update_project, get_project, get_scenes, friendly_error,
    is_cancelled, refund_on_final_failure,
)


def _download(url: str, dest: str) -> None:
    with httpx.Client(timeout=180) as client:
        resp = client.get(url)
        resp.raise_for_status()
        with open(dest, "wb") as f:
            f.write(resp.content)


@celery_app.task(bind=True, max_retries=2, default_retry_delay=45, queue="media",
                 soft_time_limit=2400, time_limit=2700)
def assemble_task(self, project_id: str):
    if is_cancelled(project_id):
        return
    project = get_project(project_id)
    if not project:
        return

    start = time.time()
    tmpdir = tempfile.mkdtemp()
    silent_path = os.path.join(tmpdir, "silent.mp4")
    audio_path = os.path.join(tmpdir, "voice.mp3")
    muxed_path = os.path.join(tmpdir, "muxed.mp4")
    final_path = os.path.join(tmpdir, "final.mp4")

    try:
        scenes = get_scenes(project_id)
        clip_urls = [s["clip_url"] for s in scenes if s.get("clip_url")]
        if not clip_urls:
            raise RuntimeError("No rendered scene clips to assemble")

        # 1) Stitch scene clips in order (crossfade when enabled for this count).
        # Transition type varies per pair by the incoming scene's emotion, and the
        # per-style grade pass rides the same encode (both config-driven).
        local_clips: list[str] = []
        for i, url in enumerate(clip_urls):
            p = os.path.join(tmpdir, f"clip_{i:03d}.mp4")
            _download(url, p)
            local_clips.append(p)
        from app.core.config import get_settings
        _s = get_settings()
        transitions: list[str] | None = None
        grade = ""
        if _s.scene_transitions_on(len(local_clips)):
            if _s.scene_transition_variety:
                import random
                from app.schemas.common import transition_for_emotion
                rng = random.Random(project_id)  # deterministic per project
                with_clips = [s for s in scenes if s.get("clip_url")]
                transitions = [
                    transition_for_emotion(s.get("emotion") or "", rng)
                    for s in with_clips[1:]
                ]
            else:
                transitions = [_s.scene_transition]
            if _s.scene_style_grade:
                from app.schemas.common import grade_filter
                grade = grade_filter(project.get("style") or "", project.get("niche") or "")
        ffmpeg_scene.concat_scene_clips(
            local_clips, silent_path,
            transitions=transitions, trans_seconds=_s.scene_transition_seconds,
            grade=grade,
        )

        # 2) Mux the voiceover onto the stitched timeline.
        if project.get("voiceover_url"):
            _download(project["voiceover_url"], audio_path)
            ffmpeg_scene.mux_audio(silent_path, audio_path, muxed_path)
        else:
            shutil.copy(silent_path, muxed_path)

        # 3) Subtitles: transcribe the narration and burn (skip if disabled).
        transcription: dict = {}
        subtitles_burned = False
        if project.get("subtitle_enabled", True) and os.path.exists(audio_path):
            # mp3 voiceover is already small; extract a 16k mono mp3 to stay under
            # Whisper's 25 MB cap on long narrations.
            trans_src = os.path.join(tmpdir, "transcribe.mp3")
            src = trans_src if _extract_audio(audio_path, trans_src) else audio_path
            transcription = asyncio.run(transcribe(src))
            style = {
                "font_color": project.get("subtitle_color", "#FFFFFF"),
                "font_size": project.get("subtitle_size") or 84,  # readable default (see SUBTITLE_SIZES)
                "placement": project.get("subtitle_position", "bottom"),
                "font_style": project.get("subtitle_font", "bold"),
                "format": project.get("format", "9:16"),
            }
            subtitles_burned = _burn_subtitles(
                muxed_path, final_path, transcription.get("segments", []), style
            )
        if not subtitles_burned:
            shutil.copy(muxed_path, final_path)

        # 4) Upload final + complete.
        object_key = f"projects/{project['user_id']}/{project_id}/final.mp4"
        final_url = upload_video(final_path, object_key)

        log_event(project_id, "assembly", "completed", int((time.time() - start) * 1000),
                  {"scenes": len(clip_urls), "subtitles_burned": subtitles_burned})
        update_project(project_id, {
            "final_video_url": final_url,
            "transcription": transcription or None,
            "status": "completed",
            "processing_time_ms": int((time.time() - start) * 1000),
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "error_message": None,
        })

    except Exception as exc:
        log_event(project_id, "assembly", "failed", metadata={"error": str(exc)})
        update_project(project_id, {"status": "failed_at_assembly",
                                    "error_message": friendly_error("assembly")})
        if self.request.retries >= self.max_retries:
            refund_on_final_failure(project)
        raise self.retry(exc=exc)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
