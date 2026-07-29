"""Cast block + sheet-usability rules (services/character_sheet.py).

Regression guard for the "Ada" drift Faith reported on 2026-07-29: one character's
vision call came back as a refusal, that character was dropped from the cast block
entirely, and the scene engine then re-invented her every scene.
"""

from app.services.character_sheet import _is_usable_sheet, cast_block, unlocked_names

SHEET = ("Elderly female-presenting character with an oval face, warm brown eyes, "
         "deep brown skin, short gray hair, medium build.")


def test_refusals_and_stubs_are_not_usable_sheets():
    for bad in ("I'm sorry, I can't help with that.",
                "I cannot assist with identifying people in images.",
                "Sorry, I am unable to provide that.",
                "short"):
        assert not _is_usable_sheet(bad), bad
    assert _is_usable_sheet(SHEET)


def test_character_without_a_sheet_stays_in_the_cast_block():
    block = cast_block([
        {"name": "Ada", "image_url": "u1", "description": None},
        {"name": "Iya Bola", "image_url": "u2", "description": SHEET},
    ])
    assert "Ada" in block                      # never silently dropped
    assert "reference image supplied for Ada" in block
    assert SHEET in block


def test_unlocked_names_reports_the_partial_lock():
    cast = [
        {"name": "Ada", "image_url": "u1", "description": None},
        {"name": "Iya Bola", "image_url": "u2", "description": SHEET},
    ]
    assert unlocked_names(cast) == ["Ada"]


def test_unnamed_references_do_not_produce_a_block():
    assert cast_block([{"name": "", "image_url": "u1", "description": None}]) == ""
    assert cast_block([]) == ""
