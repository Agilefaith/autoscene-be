"""Retry resume map (api/routes/projects.py).

Faith asked for a Retry Production button so a failed render can be resumed
without starting over. Each failure status must resume from its own stage.
"""

from app.api.routes.projects import ACTIVE_STATUSES, _RETRY_RESUME


def test_every_failure_stage_has_a_resume_target():
    for st in ("failed_at_breakdown", "failed_at_images", "failed_at_voiceover",
               "failed_at_render", "failed_at_assembly"):
        assert st in _RETRY_RESUME, st


def test_resume_targets_use_valid_queues_and_non_active_entry_status():
    for st, (module, task, next_status, queue) in _RETRY_RESUME.items():
        assert queue in {"fast", "media"}, st
        assert module.startswith("app.workers.tasks."), st
        assert task, st
        # the status we flip to must be one the pipeline treats as in-flight
        assert next_status in ACTIVE_STATUSES, st


def test_image_and_render_failures_resume_in_place_not_from_scratch():
    assert _RETRY_RESUME["failed_at_images"][1] == "generate_images_task"
    assert _RETRY_RESUME["failed_at_render"][1] == "render_scenes_task"
    assert _RETRY_RESUME["failed_at_assembly"][1] == "assemble_task"
