"""How a refused charge reaches the user.

consume_credits_atomic refuses with RAISE EXCEPTION, which PostgREST surfaces as
an APIError, not an empty result. If that is not translated into ValueError the
route's 402 handler never runs and someone who is simply out of credits gets a
500 with no explanation — which is exactly what happened in production on
2026-08-06.
"""

import pytest
from postgrest.exceptions import APIError

from app.services import credits as credits_service


class _FakeRpc:
    def __init__(self, error: Exception | None, data=None):
        self._error, self._data = error, data

    def execute(self):
        if self._error:
            raise self._error
        return type("Result", (), {"data": self._data})()


class _FakeClient:
    def __init__(self, error: Exception | None, data=None):
        self._error, self._data = error, data
        self.called_with: dict | None = None

    def rpc(self, _name, params):
        self.called_with = params
        return _FakeRpc(self._error, self._data)


def _patch_client(monkeypatch, client):
    monkeypatch.setattr(credits_service, "get_supabase_client", lambda: client)


def test_insufficient_credits_becomes_a_value_error(monkeypatch):
    err = APIError({"code": "P0001", "message": "Insufficient credits: need 22, have 3"})
    _patch_client(monkeypatch, _FakeClient(err))
    with pytest.raises(ValueError, match="Insufficient credits"):
        credits_service.consume_credits("u1", "p1", "r1", 22)


def test_an_unrelated_database_error_is_not_swallowed(monkeypatch):
    """Only the deliberate refusal is translated. A missing column or a broken
    connection must keep bubbling up as a 500 rather than masquerading as
    'out of credits'."""
    err = APIError({"code": "42703", "message": 'column "nope" does not exist'})
    _patch_client(monkeypatch, _FakeClient(err))
    with pytest.raises(APIError):
        credits_service.consume_credits("u1", "p1", "r1", 1)


def test_an_empty_result_is_still_treated_as_a_refusal(monkeypatch):
    _patch_client(monkeypatch, _FakeClient(None, data=None))
    with pytest.raises(ValueError):
        credits_service.consume_credits("u1", "p1", "r1", 1)


def test_a_successful_charge_returns_the_split(monkeypatch):
    payload = {"credits": 22, "from_plan": 20, "from_topup": 2}
    client = _FakeClient(None, data=payload)
    _patch_client(monkeypatch, client)
    assert credits_service.consume_credits("u1", "p1", "r1", 22) == payload
    assert client.called_with == {
        "p_user_id": "u1", "p_project_id": "p1", "p_request_id": "r1", "p_credits": 22,
    }
