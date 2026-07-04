"""
Shared fixtures for all tests.
- Overrides auth dependencies (no real JWT needed)
- Provides a mock Supabase client factory
- Provides a TestClient for FastAPI
"""
import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient

from app.main import app
from app.core.deps import get_current_user_id, get_current_user, get_settings as deps_get_settings
from app.core.config import get_settings


@pytest.fixture(autouse=True)
def _bypass_rate_limit():
    """Rate limiting hits real Redis + DB; bypass it for all tests by default.
    Tests that target rate limiting patch these explicitly instead."""
    with patch("app.services.ratelimit.check_request_rate", return_value=True), \
         patch("app.services.ratelimit.active_job_count", return_value=0):
        yield

TEST_USER_ID = "user-test-123"
TEST_USER_TRIAL    = {"id": TEST_USER_ID, "user_type": "trial",    "plan_tier": "free"}
TEST_USER_PRO      = {"id": TEST_USER_ID, "user_type": "standard", "plan_tier": "pro"}
TEST_USER_PREMIUM  = {"id": TEST_USER_ID, "user_type": "standard", "plan_tier": "premium"}
TEST_USER_INTERNAL = {"id": TEST_USER_ID, "user_type": "internal", "plan_tier": "free"}


def make_client(user: dict | None = None) -> TestClient:
    """Return a TestClient with auth overridden to the given user dict."""
    if user is None:
        user = TEST_USER_TRIAL
    app.dependency_overrides[get_current_user_id] = lambda: user["id"]
    app.dependency_overrides[get_current_user]    = lambda: user
    app.dependency_overrides[deps_get_settings]   = get_settings
    return TestClient(app)


@pytest.fixture
def trial_client():
    yield make_client(TEST_USER_TRIAL)
    app.dependency_overrides.clear()


@pytest.fixture
def pro_client():
    yield make_client(TEST_USER_PRO)
    app.dependency_overrides.clear()


@pytest.fixture
def premium_client():
    yield make_client(TEST_USER_PREMIUM)
    app.dependency_overrides.clear()


@pytest.fixture
def internal_client():
    yield make_client(TEST_USER_INTERNAL)
    app.dependency_overrides.clear()


def mock_supabase():
    """
    Return a MagicMock that mimics supabase-py fluent query builder.
    Table mocks are CACHED so the same name always returns the same object —
    this allows tests to configure table mocks before calling the endpoint.

    Usage:
        supabase = mock_supabase()
        supabase.table("personas").single.return_value = MagicMock(execute=lambda: ...)
    """
    client = MagicMock()
    _cache: dict[str, MagicMock] = {}

    def _make_table(name: str) -> MagicMock:
        tbl = MagicMock()
        # All chainable query methods return the same tbl so fluent chains work
        for method in ("select", "insert", "update", "delete",
                       "eq", "neq", "lte", "lt", "order", "range", "limit"):
            getattr(tbl, method).return_value = tbl

        # Default execute: return empty list
        tbl.execute.return_value = MagicMock(data=[], count=0)
        # Default single: return None
        tbl.single.return_value = MagicMock(execute=lambda: MagicMock(data=None))
        # Default maybe_single: return None
        tbl.maybe_single.return_value = MagicMock(execute=lambda: MagicMock(data=None))
        return tbl

    def _table(name: str) -> MagicMock:
        if name not in _cache:
            _cache[name] = _make_table(name)
        return _cache[name]

    client.table.side_effect = _table
    return client
