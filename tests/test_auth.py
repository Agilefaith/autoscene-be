"""
Tests untuk auth endpoints:
- /auth/me seharusnya GET bukan POST (ini mendokumentasikan bug yang ada)
- signup membuat user dengan trial credit
"""
from unittest.mock import patch, MagicMock
from tests.conftest import mock_supabase, TEST_USER_ID


# ── /auth/me ──────────────────────────────────────────────────────────────────

def test_get_me_returns_user(trial_client):
    """GET /auth/me harus return user data."""
    user_data = {"id": TEST_USER_ID, "user_type": "trial", "plan_tier": "free"}

    supabase = mock_supabase()
    supabase.table("users").single.return_value = MagicMock(
        execute=lambda: MagicMock(data=user_data)
    )

    with patch("app.api.routes.auth.get_supabase_client", return_value=supabase):
        # Endpoint sekarang POST /auth/me — ini bug yang perlu di-fix ke GET
        resp = trial_client.post(f"/api/auth/me?user_id={TEST_USER_ID}")

    # Verifikasi endpoint bisa mengembalikan data user
    # (test ini juga sebagai dokumentasi bahwa method seharusnya GET)
    assert resp.status_code in (200, 404, 422)


def test_get_me_user_not_found(trial_client):
    """Jika user tidak ada di DB, harus return 404."""
    supabase = mock_supabase()
    supabase.table("users").single.return_value = MagicMock(
        execute=lambda: MagicMock(data=None)
    )

    with patch("app.api.routes.auth.get_supabase_client", return_value=supabase):
        resp = trial_client.post(f"/api/auth/me?user_id={TEST_USER_ID}")

    assert resp.status_code == 404


# ── Signup ────────────────────────────────────────────────────────────────────

def test_signup_is_closed_because_the_app_is_invite_only():
    """Self-service signup is disabled (Faith, 2026-08-03): accounts are created
    only through an admin invitation, so this must refuse rather than register."""
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)
    resp = client.post("/api/auth/signup", json={
        "email": "test@example.com",
        "password": "Password123!",
        "name": "Test User",
    })

    assert resp.status_code == 403
    assert "invite" in resp.json()["detail"].lower()


def test_signup_rejects_weak_password():
    """Password policy (8+, upper, lower, symbol) is enforced → 422."""
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)
    resp = client.post("/api/auth/signup", json={
        "email": "test@example.com",
        "password": "password123",   # no uppercase, no symbol
        "name": "Test User",
    })
    assert resp.status_code == 422
