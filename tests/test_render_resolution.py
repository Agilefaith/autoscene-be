"""Output resolution per plan.

"Better quality" on Pro and Scale means a higher-resolution render (Faith,
2026-08-06). Every HD canvas must stay exactly proportional to its standard
counterpart, because anything sized against the canvas (most importantly the
subtitle PlayRes grid) relies on that to scale correctly.
"""

import pytest

from app.core.config import get_settings
from app.schemas.common import RENDER_DIMENSIONS, RENDER_DIMENSIONS_HD, render_dimensions

settings = get_settings()
FORMATS = ["16:9", "9:16", "1:1"]


@pytest.mark.parametrize("plan_id,expected", [
    ("starter", 1080),
    ("creator", 1080),
    ("pro", 1440),
    ("scale", 1440),
    ("", 1080),          # no subscription
    ("nonsense", 1080),  # unknown plan never gets the premium canvas
])
def test_short_edge_per_plan(plan_id, expected):
    assert settings.render_short_edge_for(plan_id) == expected


@pytest.mark.parametrize("fmt", FORMATS)
def test_hd_is_exactly_proportional_to_standard(fmt):
    sw, sh = RENDER_DIMENSIONS[fmt]
    hw, hh = RENDER_DIMENSIONS_HD[fmt]
    assert hw / sw == hh / sh, f"{fmt} HD canvas is not a uniform scale of standard"


@pytest.mark.parametrize("fmt", FORMATS)
def test_dimensions_are_even_so_h264_can_encode_them(fmt):
    for w, h in (RENDER_DIMENSIONS[fmt], RENDER_DIMENSIONS_HD[fmt]):
        assert w % 2 == 0 and h % 2 == 0


@pytest.mark.parametrize("fmt", FORMATS)
def test_render_dimensions_picks_the_right_table(fmt):
    assert render_dimensions(fmt, 1080) == RENDER_DIMENSIONS[fmt]
    assert render_dimensions(fmt, 1440) == RENDER_DIMENSIONS_HD[fmt]


def test_an_unknown_format_falls_back_to_vertical():
    assert render_dimensions("21:9", 1080) == RENDER_DIMENSIONS["9:16"]
    assert render_dimensions("21:9", 1440) == RENDER_DIMENSIONS_HD["9:16"]
