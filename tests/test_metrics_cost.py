"""Per-engine image cost (services/metrics.py).

Gemini 2.5 Flash Image was verified live at 1290 output tokens per 9:16 image, which
at the published $30 / 1M output tokens is $0.0387 — about 5x SDXL. Pricing every
image at the SDXL rate under-reported the dominant cost of every long video.
"""

from app.services.metrics import GEMINI_USD_PER_IMAGE, SDXL_USD_PER_IMAGE, image_cost


def test_gemini_is_priced_well_above_sdxl():
    assert GEMINI_USD_PER_IMAGE > SDXL_USD_PER_IMAGE * 3


def test_cost_is_split_per_engine():
    assert image_cost({"gemini": 10, "sdxl": 0}) == round(10 * GEMINI_USD_PER_IMAGE, 4)
    assert image_cost({"gemini": 0, "sdxl": 10}) == round(10 * SDXL_USD_PER_IMAGE, 4)
    mixed = image_cost({"gemini": 4, "sdxl": 6})
    assert mixed == round(4 * GEMINI_USD_PER_IMAGE + 6 * SDXL_USD_PER_IMAGE, 4)


def test_missing_or_noisy_counts_are_safe():
    assert image_cost({}) == 0.0
    assert image_cost({"gemini": None, "sdxl": -5}) == 0.0
    # the stats dict also carries non-numeric keys (fallback_reasons); ignore them
    assert image_cost({"gemini": 2, "sdxl": 0, "fallback_reasons": ["x"]}) == round(2 * GEMINI_USD_PER_IMAGE, 4)
