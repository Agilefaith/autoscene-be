"""
Tests untuk script immutability:
- Custom scripts (is_locked=True) tidak bisa di-update
- AI scripts (is_locked=False) bisa di-update
"""
from unittest.mock import patch, MagicMock
from tests.conftest import mock_supabase

UPDATE_BODY = {"title": "Updated Title"}

FULL_SCRIPT_ROW = {
    "id": "script-001",
    "user_id": "user-test-123",
    "title": "My Script",
    "content": "Script content here",
    "generation_mode": "ai",
    "is_locked": False,
    "created_at": "2026-01-01T00:00:00+00:00",
}


def test_custom_script_update_blocked(trial_client):
    """Custom script (is_locked=True) harus return 403."""
    locked_script = {"is_locked": True, "user_id": "user-test-123"}

    supabase = mock_supabase()
    supabase.table("scripts").single.return_value = MagicMock(
        execute=lambda: MagicMock(data=locked_script)
    )

    with patch("app.api.routes.scripts.get_supabase_client", return_value=supabase):
        resp = trial_client.put("/api/scripts/script-001", json=UPDATE_BODY)

    assert resp.status_code == 403
    assert "immutable" in resp.json()["detail"].lower()


def test_ai_script_update_allowed(trial_client):
    """AI script (is_locked=False) boleh di-update."""
    unlocked_script = {"is_locked": False, "user_id": "user-test-123"}
    updated_row = {**FULL_SCRIPT_ROW, "title": "Updated Title"}

    supabase = mock_supabase()
    supabase.table("scripts").single.return_value = MagicMock(
        execute=lambda: MagicMock(data=unlocked_script)
    )
    supabase.table("scripts").execute.return_value = MagicMock(data=[updated_row])

    with patch("app.api.routes.scripts.get_supabase_client", return_value=supabase):
        resp = trial_client.put("/api/scripts/script-001", json=UPDATE_BODY)

    assert resp.status_code == 200


def test_script_not_found_returns_404(trial_client):
    supabase = mock_supabase()
    supabase.table("scripts").single.return_value = MagicMock(
        execute=lambda: MagicMock(data=None)
    )

    with patch("app.api.routes.scripts.get_supabase_client", return_value=supabase):
        resp = trial_client.put("/api/scripts/nonexistent", json=UPDATE_BODY)

    assert resp.status_code == 404


def test_custom_script_saved_with_lock(trial_client):
    """Saat save script mode='custom', is_locked harus True."""
    saved_row = {**FULL_SCRIPT_ROW, "generation_mode": "custom", "is_locked": True}

    supabase = mock_supabase()
    supabase.table("scripts").execute.return_value = MagicMock(data=[saved_row])

    with patch("app.api.routes.scripts.get_supabase_client", return_value=supabase):
        resp = trial_client.post("/api/scripts", json={
            "title": "My Script",
            "content": "Script content here",
            "mode": "custom",
        })

    assert resp.status_code == 201
    call_args = supabase.table("scripts").insert.call_args[0][0]
    assert call_args["is_locked"] is True


def test_ai_script_saved_without_lock(trial_client):
    """Saat save script mode='ai', is_locked harus False."""
    saved_row = {**FULL_SCRIPT_ROW, "generation_mode": "ai", "is_locked": False}

    supabase = mock_supabase()
    supabase.table("scripts").execute.return_value = MagicMock(data=[saved_row])

    with patch("app.api.routes.scripts.get_supabase_client", return_value=supabase):
        resp = trial_client.post("/api/scripts", json={
            "title": "AI Script",
            "content": "Script content here",
            "mode": "ai",
        })

    assert resp.status_code == 201
    call_args = supabase.table("scripts").insert.call_args[0][0]
    assert call_args["is_locked"] is False


# ── AI generation prompt: Style/Goal/Tone differentiation ─────────────────────

import pytest


@pytest.mark.asyncio
async def test_generate_script_injects_style_goal_tone_guidance():
    """Style/Goal/Tone must inject explicit guidance into the GPT prompt so each
    choice produces a visibly distinct script (not just a free-text label)."""
    import app.services.openai_service as svc

    captured = {}

    async def fake_create(*args, **kwargs):
        captured["user"] = kwargs["messages"][1]["content"]
        resp = MagicMock()
        resp.choices = [MagicMock(message=MagicMock(content="hook line."))]
        return resp

    with patch.object(svc._client.chat.completions, "create", side_effect=fake_create):
        await svc.generate_script(
            title="T", product_name="P", target_audience="A",
            tone="energetic", goal="education", style="tiktok_hook",
            target_duration_seconds=30,
        )

    u = captured["user"]
    assert svc.STYLE_GUIDANCE["tiktok_hook"] in u
    assert svc.GOAL_GUIDANCE["education"] in u
    assert svc.TONE_GUIDANCE["energetic"] in u


@pytest.mark.asyncio
async def test_generate_script_handles_unknown_values_gracefully():
    """Unknown style/goal/tone fall back to the raw label without crashing."""
    import app.services.openai_service as svc

    captured = {}

    async def fake_create(*args, **kwargs):
        captured["user"] = kwargs["messages"][1]["content"]
        resp = MagicMock()
        resp.choices = [MagicMock(message=MagicMock(content="x"))]
        return resp

    with patch.object(svc._client.chat.completions, "create", side_effect=fake_create):
        await svc.generate_script(
            title="T", product_name="P", target_audience="A",
            tone="quirky", goal="awareness", style="vlog",
            target_duration_seconds=30,
        )

    u = captured["user"]
    assert "vlog" in u and "awareness" in u and "quirky" in u
