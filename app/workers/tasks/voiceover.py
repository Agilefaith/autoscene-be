"""Voiceover stage (AutoScene §4.5).

Generates one narration track for the whole script, then SYNCS the visuals to it:
each scene's on-screen time is read off Whisper's word-level timestamps for the
real audio, not estimated (PRD Definition of Done: "audio is synced"). Runs
before the scene render so each clip is rendered at its synced duration.
"""

import asyncio
import os
import tempfile
import time

from app.core.celery_app import celery_app
from app.services.supabase import get_supabase_client
from app.services.backblaze import upload_bytes
from app.services.ffmpeg_scene import probe_duration
from app.services.ffmpeg_subs import _extract_audio
from app.services.whisper import transcribe, scene_durations_from_transcript
from app.workers.tasks.project_common import (
    log_event, update_project, get_project, get_scenes, friendly_error,
    is_cancelled, refund_on_final_failure,
)

# Minimum on-screen time per scene so a short clip is never sub-second.
_MIN_SCENE_SECONDS = 2.0


def _word_count_durations(scenes: list[dict], audio_seconds: float) -> list[float]:
    """Fallback when transcription fails: split audio_seconds across scenes
    proportional to word count, assuming a constant speaking rate.

    This is the ORIGINAL syncing method, kept only as a safety net — it's what
    drifted several seconds off the real narration on a long video (Faith,
    2026-08-16), because real speech isn't a constant rate. It still beats not
    rendering at all when Whisper is unavailable.
    """
    weights = [max(1, len((s.get("scene_text") or "").split())) for s in scenes]
    total_w = sum(weights) or 1
    return [max(_MIN_SCENE_SECONDS, round(audio_seconds * w / total_w, 2)) for w in weights]


async def _synthesize(provider: str, voice_id: str, text: str,
                      api_key: str | None = None) -> bytes:
    """`api_key` is the user's own provider key when the voice config carries one
    (private voices are only reachable with their owner's key)."""
    if provider == "minimax":
        from app.services.minimax import generate_tts_audio as mm_tts
        audio = await mm_tts(voice_id, text, api_key=api_key)
        if not audio:
            raise RuntimeError("Minimax TTS returned no audio")
        return audio
    from app.services.elevenlabs import generate_tts_audio as el_tts
    return await el_tts(voice_id, text, api_key=api_key)  # raises with detail on failure


@celery_app.task(bind=True, max_retries=3, default_retry_delay=45, queue="media")
def generate_voiceover_task(self, project_id: str):
    if is_cancelled(project_id):
        return
    project = get_project(project_id)
    if not project:
        return

    start = time.time()
    client = get_supabase_client()
    try:
        script = (
            client.table("scripts").select("content")
            .eq("id", project["script_id"]).single().execute().data
        )
        text = (script or {}).get("content") or ""
        voice = (
            client.table("voice_configs").select("provider, voice_id, api_key_encrypted")
            .eq("id", project["voice_config_id"]).single().execute().data
        )
        if not voice:
            raise RuntimeError("Voice configuration not found")

        from app.services.crypto import decrypt
        audio = asyncio.run(_synthesize(
            voice["provider"], voice["voice_id"], text,
            api_key=decrypt(voice.get("api_key_encrypted")),
        ))

        # Upload the voiceover.
        key = f"projects/{project['user_id']}/{project['id']}/voiceover.mp3"
        voiceover_url = upload_bytes(audio, key, "audio/mpeg")

        # Probe the real duration, then sync visuals to it: transcribe the audio
        # and read each scene's on-screen time off where its words actually land
        # in time, not a word-count estimate (Faith, 2026-08-16 — the estimate
        # drifted up to ~18s off the real narration by the middle of a long
        # video). The transcript is stored on the project so assembly's
        # subtitle burn reuses it instead of transcribing the same audio twice.
        scenes = get_scenes(project_id)
        tmpdir = tempfile.mkdtemp()
        try:
            audio_path = os.path.join(tmpdir, "voice.mp3")
            with open(audio_path, "wb") as f:
                f.write(audio)
            audio_seconds = probe_duration(audio_path) or float(project["duration_seconds"])

            transcription: dict | None = None
            try:
                small_audio = os.path.join(tmpdir, "transcribe.mp3")
                trans_src = small_audio if _extract_audio(audio_path, small_audio) else audio_path
                cast_names = [c.get("name") for c in (project.get("characters") or []) if c.get("name")]
                transcription = asyncio.run(transcribe(trans_src, vocabulary=cast_names))
                durations = scene_durations_from_transcript(
                    scenes, transcription["segments"], audio_seconds, _MIN_SCENE_SECONDS)
            except Exception:
                # Whisper hiccup shouldn't brick the render — fall back to the
                # old estimate rather than failing the whole video.
                transcription = None
                durations = _word_count_durations(scenes, audio_seconds)
        finally:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)

        for s, dur in zip(scenes, durations):
            client.table("scenes").update(
                {"duration_seconds": dur}
            ).eq("id", s["id"]).execute()

        update_project(project_id, {
            "voiceover_url": voiceover_url,
            "status": "rendering_scenes",
            "transcription": transcription,
        })
        log_event(project_id, "voiceover", "completed",
                  int((time.time() - start) * 1000),
                  {"audio_seconds": round(audio_seconds, 2), "scenes": len(scenes),
                   "synced_from_transcript": transcription is not None})

        from app.workers.tasks.scene_render import render_scenes_task
        render_scenes_task.apply_async(args=[project_id], queue="media")

    except Exception as exc:
        log_event(project_id, "voiceover", "failed", metadata={"error": str(exc)})
        update_project(project_id, {"status": "failed_at_voiceover",
                                    "error_message": friendly_error("voiceover")})
        if self.request.retries >= self.max_retries:
            refund_on_final_failure(project)
        raise self.retry(exc=exc)
