"""What actually reaches the image model when the user writes the prompt.

Faith's complaint was that the AI's *scene description* drifted from the
narration, so the user now writes that part. The style block and lock line are
not interpretation and must survive: the style block is the only thing making
every scene in one video share a look (and the only thing making the Video Style
picker mean anything), and the lock line is the guard against split panels and
collages. Stripping them once let a Cartoon project render photoreal shots.
"""

from app.schemas.common import style_prompt
from app.services.scene_engine import decorate_user_prompt
from app.workers.tasks.image_gen import _prompts_for

USER_TEXT = "A tall stone lighthouse against a stormy dusk sky, cinematic wide shot"


def test_the_users_words_survive_verbatim():
    out = decorate_user_prompt(USER_TEXT, "cartoon")
    assert out.startswith(USER_TEXT), "the user's text must lead, unrewritten"


def test_the_chosen_style_is_applied():
    cartoon = decorate_user_prompt(USER_TEXT, "cartoon")
    cinematic = decorate_user_prompt(USER_TEXT, "cinematic")
    assert style_prompt("cartoon")[:40] in cartoon
    assert style_prompt("cinematic")[:40] in cinematic
    assert cartoon != cinematic, "the Video Style picker must change the prompt"


def test_the_lock_line_is_applied():
    out = decorate_user_prompt(USER_TEXT, "cartoon")
    assert "no split panels" in out
    assert "no collage" in out


def test_an_unknown_style_still_produces_a_usable_prompt():
    out = decorate_user_prompt(USER_TEXT, "not-a-style")
    assert out.startswith(USER_TEXT)
    assert "no split panels" in out


def test_an_empty_prompt_stays_empty():
    assert decorate_user_prompt("", "cartoon") == ""
    assert decorate_user_prompt("   ", "cartoon") == ""


def test_the_render_stage_decorates_the_user_prompt():
    out = _prompts_for({"user_prompt": USER_TEXT}, "cartoon")
    assert len(out) == 1
    assert out[0].startswith(USER_TEXT)
    assert style_prompt("cartoon")[:40] in out[0]


def test_an_older_project_uses_its_already_decorated_ai_prompt():
    """Pre-override scenes were decorated at breakdown time; decorating again
    would duplicate the style block."""
    ai = "Some scene. cinematic realistic style. The image must exactly match..."
    assert _prompts_for({"image_prompt": ai}, "cartoon") == [ai]
    assert _prompts_for({"user_prompt": "  ", "image_prompt": ai}, "cartoon") == [ai]
