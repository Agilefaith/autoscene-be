"""Admin invite gating (api/routes/admin.py, core/deps.py).

AutoScene is invite-only (Faith, 2026-08-03): only the admin account can invite,
and the invite carries the plan the new user lands on.
"""

import pytest
from fastapi import HTTPException

from app.api.routes.admin import InviteRequest
from app.core.config import PLANS
from app.core.deps import require_admin


@pytest.mark.asyncio
async def test_non_admin_is_refused():
    for role in ("user", "", None):
        with pytest.raises(HTTPException) as exc:
            await require_admin({"id": "u1", "role": role})
        assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_admin_is_allowed_through():
    user = {"id": "u1", "role": "admin"}
    assert await require_admin(user) is user


def test_invite_defaults_to_the_free_plan():
    req = InviteRequest(email="someone@example.com")
    assert req.plan_id == "free"
    assert req.plan_id in PLANS


def test_invite_accepts_every_real_plan():
    for plan_id in PLANS:
        assert InviteRequest(email="a@b.com", plan_id=plan_id).plan_id == plan_id


def test_invite_rejects_a_malformed_address():
    with pytest.raises(Exception):
        InviteRequest(email="not-an-email")
