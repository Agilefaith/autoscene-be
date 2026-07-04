"""
Tests untuk AutoScene cost-metric formula:
  units = ceil(duration_seconds / 30) × mode_multiplier
  Mode 1 multiplier = 1, Mode 2 multiplier = 3 (config-driven).
  NOTE: billing is now a per-video quota (see consume_video_quota); this formula
  only feeds the internal cost metric (Mode 2 renders ~3x the images of Mode 1).
"""
import pytest
from app.services.credits import calculate_project_credits


@pytest.mark.parametrize("duration, mode, expected", [
    # Mode 1 (multiplier=1)
    (30,  "mode_1", 1),   # 1 unit × 1
    (60,  "mode_1", 2),   # 2 units × 1
    (45,  "mode_1", 2),   # ceil(45/30)=2 × 1
    (1,   "mode_1", 1),   # ceil(1/30)=1 × 1 — minimum 1 unit
    (90,  "mode_1", 3),   # 3 units × 1
    (120, "mode_1", 4),   # 4 units × 1
    # Mode 2 (multiplier=3)
    (30,  "mode_2", 3),   # 1 unit × 3
    (60,  "mode_2", 6),   # 2 units × 3
    (45,  "mode_2", 6),   # ceil(45/30)=2 × 3
    (1,   "mode_2", 3),   # ceil(1/30)=1 × 3 — minimum 1 unit
    (90,  "mode_2", 9),   # 3 units × 3
    (120, "mode_2", 12),  # 4 units × 3
])
def test_calculate_project_credits(duration, mode, expected):
    assert calculate_project_credits(duration, mode) == expected


def test_unknown_mode_defaults_to_mode_1():
    """Mode yang tidak dikenal harus fallback ke multiplier Mode 1 (=1)."""
    assert calculate_project_credits(30, "unknown_mode") == 1


def test_credits_always_positive():
    """Credits tidak boleh nol atau negatif."""
    assert calculate_project_credits(1, "mode_1") > 0
    assert calculate_project_credits(1, "mode_2") > 0


def test_modes_have_different_costs():
    assert calculate_project_credits(30, "mode_1") != calculate_project_credits(30, "mode_2")
