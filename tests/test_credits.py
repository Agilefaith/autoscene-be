"""Credit costing.

Billing is in minutes (Faith, 2026-08-05): one credit buys one minute of
finished video, and any started minute is charged in full. There is no
render-mode multiplier any more — Mode 1 is the only mode.
"""
import pytest

from app.core.config import PLANS, TOPUP_PACKS, get_settings
from app.services.credits import calculate_project_credits


@pytest.mark.parametrize("duration, expected", [
    (1,    1),   # anything at all costs a full minute
    (30,   1),
    (60,   1),
    (61,   2),   # a started minute is charged in full
    (90,   2),
    (120,  2),
    (1800, 30),  # 30 min
    (2400, 40),  # 40 min — the hard ceiling
])
def test_calculate_project_credits(duration, expected):
    assert calculate_project_credits(duration) == expected


def test_credits_are_always_positive():
    assert calculate_project_credits(0) >= 1
    assert calculate_project_credits(1) >= 1


def test_a_plan_covers_its_advertised_minutes():
    """credits_per_month doubles as minutes-per-month, so the arithmetic has to
    line up: a 20-credit plan buys exactly one 20-minute video."""
    for plan in PLANS.values():
        assert calculate_project_credits(plan.credits_per_month * 60) == plan.credits_per_month


def test_the_catalog_has_no_free_tier():
    assert "free" not in PLANS
    assert get_settings().plan("free") is None


def test_topup_packs_are_well_formed():
    assert TOPUP_PACKS
    for pack in TOPUP_PACKS.values():
        assert pack.credits > 0 and pack.price_ngn > 0
