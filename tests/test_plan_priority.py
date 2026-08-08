"""Per-plan queue priority (Faith's pricing sheet: Standard / Faster / Priority)."""

from app.api.routes.projects import PLAN_PRIORITY, PRIORITY_FREE
from app.core.celery_app import celery_app
from app.core.config import PLANS


def test_paid_tiers_are_ordered():
    # Pro/Scale (priority) < Creator (faster) < Starter (standard) — lower drains
    # first. Anyone without a live subscription falls back to PRIORITY_FREE.
    assert PLAN_PRIORITY["scale"] == PLAN_PRIORITY["pro"]
    assert PLAN_PRIORITY["scale"] < PLAN_PRIORITY["creator"] < PLAN_PRIORITY["starter"]
    assert PLAN_PRIORITY["starter"] < PRIORITY_FREE


def test_every_plan_has_a_priority_within_broker_steps():
    steps = celery_app.conf.broker_transport_options["priority_steps"]
    for plan_id in PLANS:
        assert PLAN_PRIORITY.get(plan_id, PRIORITY_FREE) in steps
