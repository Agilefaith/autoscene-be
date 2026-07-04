"""Voiceover stage (AutoScene §4.5).

Generates one narration track for the whole script, then SYNCS the visuals to it:
the audio's true duration is divided across the scenes so the stitched video length
matches the narration (PRD Definition of Done: "audio is synced"). Runs before the
scene render so each clip is rendered at its synced duration.
"""

import asyncio
import os
import tempfile
import time

from app.core.celery_app import celery_app
from app.services.supabase import get_supabase_client
from app.services.backblaze import upload_bytes
from app.services.ffmpeg_scene import probe_duration
from app.workers.tasks.project_common import (
    log_event, update_project, get_project, get_scenes, friendly_error,
    is_cancelled, refund_on_final_failure,
)

# Minimum on-screen time per scene so a short clip is never sub-second.
_MIN_SCENE_SECONDS = 2.0


async def _synthesize(provider: str, voice_id: str, text: str) -> bytes:
    if provider == "minimax":
        from app.services.minimax import generate_tts_audio as mm_tts
        audio = await mm_tts(voice_id, text)
        if not audio:
            raise RuntimeError("Minimax TTS returned no audio")
        return audio
    from app.services.elevenlabs import generate_tts_audio as el_tts
    return await el_tts(voice_id, text)  # raises with detail on failure


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
            client.table("voice_configs").select("provider, voice_id")
            .eq("id", project["voice_config_id"]).single().execute().data
        )
        if not voice:
            raise RuntimeError("Voice configuration not found")

        audio = asyncio.run(_synthesize(voice["provider"], voice["voice_id"], text))

        # Probe the real duration to sync the visuals.
        tmpdir = tempfile.mkdtemp()
        try:
            audio_path = os.path.join(tmpdir, "voice.mp3")
            with open(audio_path, "wb") as f:
                f.write(audio)
            audio_seconds = probe_duration(audio_path) or float(project["duration_seconds"])
        finally:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)

        # Upload the voiceover.
        key = f"projects/{project['user_id']}/{project['id']}/voiceover.mp3"
        voiceover_url = upload_bytes(audio, key, "audio/mpeg")

        # Distribute audio length evenly across scenes (synced visual timeline).
        scenes = get_scenes(project_id)
        n = max(1, len(scenes))
        per_scene = max(_MIN_SCENE_SECONDS, round(audio_seconds / n, 2))
        for s in scenes:
            client.table("scenes").update(
                {"duration_seconds": per_scene}
            ).eq("id", s["id"]).execute()

        update_project(project_id, {"voiceover_url": voiceover_url, "status": "rendering_scenes"})
        log_event(project_id, "voiceover", "completed",
                  int((time.time() - start) * 1000),
                  {"audio_seconds": round(audio_seconds, 2), "per_scene": per_scene})

        from app.workers.tasks.scene_render import render_scenes_task
        render_scenes_task.apply_async(args=[project_id], queue="media")

    except Exception as exc:
        log_event(project_id, "voiceover", "failed", metadata={"error": str(exc)})
        update_project(project_id, {"status": "failed_at_voiceover",
                                    "error_message": friendly_error("voiceover")})
        if self.request.retries >= self.max_retries:
            refund_on_final_failure(project)
        raise self.retry(exc=exc)
