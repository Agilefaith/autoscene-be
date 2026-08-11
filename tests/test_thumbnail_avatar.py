"""Optional avatar/face upload on the thumbnail cloner (Faith, 2026-08-11).

Only additive: cloning with just a reference thumbnail must behave exactly as
before, and generate_image_gemini's mixed-mime references must not corrupt the
existing (name, bytes) 2-tuple call sites.
"""

import base64

import httpx

from app.api.routes.thumbnails import _AVATAR_DIRECTIVE, _CLONE_PROMPT, wanted_headline
from app.services.gemini_image import generate_image_gemini


class _FakeResponse:
    status_code = 200
    text = ""

    def json(self):
        return {"candidates": [{"content": {"parts": [
            {"inlineData": {"data": base64.b64encode(b"fake-image-bytes").decode()}}
        ]}}]}


class _FakeClient:
    """Stands in for httpx.Client(...) as a context manager, capturing the
    request body so tests can inspect exactly what was sent to Gemini."""
    last_body: dict | None = None

    def __init__(self, *a, **kw): pass
    def __enter__(self): return self
    def __exit__(self, *a): return False

    def post(self, url, params=None, headers=None, json=None):
        _FakeClient.last_body = json
        return _FakeResponse()


def _run(monkeypatch, **kwargs) -> list[dict]:
    monkeypatch.setattr(httpx, "Client", _FakeClient)
    generate_image_gemini(**kwargs)
    return _FakeClient.last_body["contents"][0]["parts"]


def test_a_single_reference_still_uses_the_global_mime(monkeypatch):
    """The existing 2-tuple call shape (character cast) must be untouched."""
    parts = _run(monkeypatch, prompt="a prompt", fmt="16:9",
                references=[("Ada", b"fake-png-bytes")], reference_mime="image/png")
    mimes = [p["inline_data"]["mime_type"] for p in parts if "inline_data" in p]
    assert mimes == ["image/png"]


def test_mixed_mime_references_each_keep_their_own_type(monkeypatch):
    """The thumbnail cloner attaches a jpeg reference and a webp avatar in the
    same call — each must be tagged with its own mime, not the global default."""
    parts = _run(monkeypatch, prompt="a prompt", fmt="16:9", references=[
        ("", b"fake-jpeg-bytes", "image/jpeg"),
        ("the person to feature in this thumbnail", b"fake-webp-bytes", "image/webp"),
    ])
    mimes = [p["inline_data"]["mime_type"] for p in parts if "inline_data" in p]
    assert mimes == ["image/jpeg", "image/webp"]


def test_the_avatar_label_is_attached_before_its_image(monkeypatch):
    parts = _run(monkeypatch, prompt="a prompt", fmt="16:9", references=[
        ("the person to feature in this thumbnail", b"fake-bytes", "image/png"),
    ])
    label_idx = next(i for i, p in enumerate(parts) if "text" in p and "the person to feature" in p["text"])
    image_idx = next(i for i, p in enumerate(parts) if "inline_data" in p)
    assert label_idx < image_idx


def test_unnamed_references_get_no_label_text(monkeypatch):
    """The primary thumbnail reference is unnamed and must stay unlabeled —
    only the avatar gets a text part in front of it."""
    parts = _run(monkeypatch, prompt="a prompt", fmt="16:9",
                references=[("", b"fake-bytes", "image/jpeg")])
    assert parts == [{"text": "a prompt"},
                     {"inline_data": {"mime_type": "image/jpeg",
                                      "data": base64.b64encode(b"fake-bytes").decode()}}]


def test_prompt_only_mentions_the_avatar_when_one_is_attached():
    without = _CLONE_PROMPT.format(instructions="make it darker", text_directive="")
    with_avatar = _CLONE_PROMPT.format(instructions="make it darker", text_directive=_AVATAR_DIRECTIVE)
    assert "second reference image" not in without
    assert "second reference image" in with_avatar


def test_headline_quoting_is_unaffected_by_the_avatar_change():
    assert wanted_headline('make it say "NO JAGUARS" in bold') == "NO JAGUARS"
    assert wanted_headline("no quotes here") == ""
