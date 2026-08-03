"""Admin routes — invite-only access control (Faith, 2026-08-03).

The app is private. Nobody signs themselves up; the admin invites a person by
email and chooses the plan they land on. Supabase sends the invitation and the
invitee sets their own password, so we never handle or transmit one.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, EmailStr

from app.core.config import PLANS
from app.core.deps import CurrentAdmin
from app.services.supabase import get_supabase_client

router = APIRouter(prefix="/admin", tags=["admin"])


class InviteRequest(BaseModel):
    email: EmailStr
    plan_id: str = "free"


class InviteResponse(BaseModel):
    id: str
    email: str
    plan_id: str
    status: str
    created_at: datetime
    accepted_at: datetime | None = None


@router.get("/invites", response_model=list[InviteResponse])
async def list_invites(_admin: CurrentAdmin, limit: int = 100):
    """Invitations, newest first, with pending ones resolved against real sign-ins.

    Acceptance is settled here rather than on every authenticated request: only
    the admin reads this list, so it costs nothing on the hot path."""
    client = get_supabase_client()
    rows = (
        client.table("invites").select("*")
        .order("created_at", desc=True).limit(limit).execute().data or []
    )
    pending = [r for r in rows if r["status"] == "pending"]
    if pending:
        try:
            signed_in = {
                str(u.id) for u in client.auth.admin.list_users()
                if getattr(u, "last_sign_in_at", None)
            }
        except Exception:
            signed_in = set()
        now = datetime.now(timezone.utc).isoformat()
        for r in pending:
            if r.get("user_id") in signed_in:
                r["status"], r["accepted_at"] = "accepted", now
                client.table("invites").update(
                    {"status": "accepted", "accepted_at": now}
                ).eq("id", r["id"]).execute()
    return rows


@router.post("/invites", status_code=status.HTTP_201_CREATED, response_model=InviteResponse)
async def create_invite(body: InviteRequest, admin: CurrentAdmin):
    """Invite someone by email on a chosen plan.

    Supabase mails the invitation and the invitee sets their own password. Their
    `users` row is created here with the chosen plan and matching video quota, so
    the plan is already in place the moment they first sign in.
    """
    if body.plan_id not in PLANS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail=f"Unknown plan '{body.plan_id}'.")
    email = str(body.email).strip().lower()
    client = get_supabase_client()

    existing = (
        client.table("invites").select("id")
        .eq("status", "pending").ilike("email", email).limit(1).execute().data
    )
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="That address already has a pending invitation.")

    try:
        invited = client.auth.admin.invite_user_by_email(email)
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY,
                            detail=f"Could not send the invitation: {str(e)[:200]}")

    user_id = getattr(getattr(invited, "user", None), "id", None)
    plan = PLANS[body.plan_id]
    try:
        _provision(client, user_id, email, body.plan_id, plan)
    except Exception as e:
        # Roll the auth user back, or the address is stuck as "already registered".
        if user_id:
            try:
                client.auth.admin.delete_user(user_id)
            except Exception:
                pass
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY,
                            detail=f"Could not provision the account: {str(e)[:200]}")

    row = client.table("invites").insert({
        "email": email,
        "plan_id": body.plan_id,
        "status": "pending",
        "invited_by": admin["id"],
        "user_id": user_id,
    }).execute().data[0]
    return row


def _provision(client, user_id: str | None, email: str, plan_id: str, plan) -> None:
    """Create the invitee's row and quota so their plan is live on first sign-in.

    `credits` is upserted on `user_id` because that is where its unique constraint
    lives; upserting on the primary key instead raises a duplicate-key error for
    anyone who already has a credits row.
    """
    if not user_id:
        return
    client.table("users").upsert({
        "id": user_id,
        "email": email,
        "role": "user",
        "user_type": "trial" if plan_id == "free" else "standard",
        "plan_tier": plan_id,
    }).execute()
    client.table("credits").upsert({
        "user_id": user_id,
        "balance": plan.videos_per_month,
        "monthly_quota": plan.videos_per_month,
    }, on_conflict="user_id").execute()


@router.post("/invites/{invite_id}/revoke", response_model=InviteResponse)
async def revoke_invite(invite_id: str, _admin: CurrentAdmin):
    """Revoke a pending invitation and delete the account it provisioned."""
    client = get_supabase_client()
    invite = (
        client.table("invites").select("*").eq("id", invite_id).single().execute().data
    )
    if not invite:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invite not found")
    if invite["status"] != "pending":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail=f"This invitation is already {invite['status']}.")

    if invite.get("user_id"):
        try:
            client.auth.admin.delete_user(invite["user_id"])
        except Exception:
            pass  # the auth user may already be gone; the invite still gets revoked

    return (
        client.table("invites")
        .update({"status": "revoked"}).eq("id", invite_id).execute().data[0]
    )


class AdminUser(BaseModel):
    id: str
    email: str | None = None
    role: str
    plan_tier: str | None = None
    user_type: str | None = None
    created_at: datetime | None = None


@router.get("/users", response_model=list[AdminUser])
async def list_users(_admin: CurrentAdmin, limit: int = 200):
    return (
        get_supabase_client().table("users")
        .select("id, email, role, plan_tier, user_type, created_at")
        .order("created_at", desc=True).limit(limit).execute().data or []
    )


class PlanChange(BaseModel):
    plan_id: str


@router.patch("/users/{user_id}/plan", response_model=AdminUser)
async def set_user_plan(user_id: str, body: PlanChange, _admin: CurrentAdmin):
    """Move a user onto another plan and refill their quota to match it."""
    if body.plan_id not in PLANS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail=f"Unknown plan '{body.plan_id}'.")
    client = get_supabase_client()
    plan = PLANS[body.plan_id]
    updated = (
        client.table("users").update({
            "plan_tier": body.plan_id,
            "user_type": "trial" if body.plan_id == "free" else "standard",
        }).eq("id", user_id).execute().data
    )
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    client.table("credits").upsert({
        "user_id": user_id,
        "balance": plan.videos_per_month,
        "monthly_quota": plan.videos_per_month,
    }, on_conflict="user_id").execute()
    return updated[0]


@router.get("/plans")
async def list_plans(_admin: CurrentAdmin):
    """Plan options for the invite form."""
    return [
        {"id": p.id, "name": p.name, "price_usd": p.price_usd,
         "videos_per_month": p.videos_per_month,
         "max_minutes": p.max_duration_seconds // 60}
        for p in PLANS.values()
    ]
