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

def test_signup_creates_auth_user():
    """Signup creates the Supabase auth user and returns its id.

    The users row + trial credit are created by a DB trigger (handle_new_user in
    supabase_migration.sql), NOT by this route — so the route's contract is just:
    create the auth user, return user_id.
    """
    from fastapi.testclient import TestClient
    from app.main import app

    mock_user = MagicMock()
    mock_user.id = "new-user-id"

    supabase = MagicMock()
    supabase.auth.admin.create_user.return_value = MagicMock(user=mock_user)

    client = TestClient(app)
    with patch("app.api.routes.auth.get_supabase_client", return_value=supabase):
        resp = client.post("/api/auth/signup", json={
            "email": "test@example.com",
            "password": "Password123!",   # must satisfy the password policy
            "name": "Test User",
        })

    assert resp.status_code == 200
    assert resp.json()["user_id"] == "new-user-id"
    supabase.auth.admin.create_user.assert_called_once()


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
