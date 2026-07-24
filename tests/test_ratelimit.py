"""Concurrency-cap statuses (services/ratelimit.py).

Regression guard for the free-tier deadlock: a project sitting at `scenes_ready`
(breakdown done, waiting for the user to hit Generate) must NOT count toward the
concurrency cap, or a free user (max_concurrent=1) trips `active >= 1` against
their own project at generate time and can never render anything.
"""

from app.services.ratelimit import _ACTIVE_PROJECT_STATUSES
from app.api.routes.projects import ACTIVE_STATUSES


def test_scenes_ready_is_not_counted_as_active():
    assert "scenes_ready" not in _ACTIVE_PROJECT_STATUSES
    assert "draft" not in _ACTIVE_PROJECT_STATUSES


def test_ratelimit_active_statuses_match_routes():
    # The two sources of truth must agree on what "in-flight" means.
    assert set(_ACTIVE_PROJECT_STATUSES) == set(ACTIVE_STATUSES)


def test_real_render_stages_still_counted():
    for s in ("pending", "generating_images", "rendering_scenes", "voiceover", "assembling"):
        assert s in _ACTIVE_PROJECT_STATUSES
