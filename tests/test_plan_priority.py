"""Per-plan queue priority (Faith's pricing sheet: Standard / Faster / Priority)."""

from app.api.routes.projects import PLAN_PRIORITY, PRIORITY_FREE
from app.core.celery_app import celery_app
from app.core.config import PLANS


def test_three_paid_tiers_are_ordered():
    # Scale (priority) < Creator (faster) < Starter (standard) < free — lower drains first.
    assert PLAN_PRIORITY["scale"] < PLAN_PRIORITY["creator"] < PLAN_PRIORITY["starter"] < PLAN_PRIORITY["free"]
    assert PLAN_PRIORITY["scale_m2"] == PLAN_PRIORITY["scale"]
    assert PLAN_PRIORITY["creator_m2"] == PLAN_PRIORITY["creator"]


def test_every_plan_has_a_priority_within_broker_steps():
    steps = celery_app.conf.broker_transport_options["priority_steps"]
    for plan_id in PLANS:
        assert PLAN_PRIORITY.get(plan_id, PRIORITY_FREE) in steps
