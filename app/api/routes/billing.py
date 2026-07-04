import json

from fastapi import APIRouter, Request, HTTPException, status
from app.core.deps import CurrentUserId, CurrentUser, AppSettings
from app.schemas.billing import (
    BillingUsageResponse,
    CheckoutRequest,
    CheckoutResponse,
    CreditTransactionResponse,
    PortalResponse,
)
from app.services import lemonsqueezy
from app.services.supabase import get_supabase_client

router = APIRouter(prefix="/billing", tags=["billing"])


@router.get("/usage", response_model=BillingUsageResponse)
async def get_billing_usage(user_id: CurrentUserId, user: CurrentUser):
    client = get_supabase_client()

    credit_row = (
        client.table("credits")
        .select("balance, monthly_quota, reset_date")
        .eq("user_id", user_id)
        .single()
        .execute()
    )
    credits = credit_row.data or {"balance": 0, "monthly_quota": None, "reset_date": None}

    video_count = (
        client.table("projects")
        .select("id", count="exact")  # type: ignore[arg-type]
        .eq("user_id", user_id)
        .eq("status", "completed")
        .execute()
    )

    return BillingUsageResponse(
        credit_balance=credits["balance"],
        monthly_quota=credits.get("monthly_quota"),
        plan_tier=user.get("plan_tier", "free"),
        user_type=user.get("user_type", "trial"),
        videos_generated=video_count.count or 0,
        reset_date=credits.get("reset_date"),
    )


@router.get("/transactions", response_model=list[CreditTransactionResponse])
async def get_transactions(user_id: CurrentUserId, limit: int = 20):
    client = get_supabase_client()
    result = (
        client.table("credit_transactions")
        .select("*")
        .eq("user_id", user_id)
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    return result.data or []


@router.post("/checkout", response_model=CheckoutResponse)
async def create_checkout(req: CheckoutRequest, user_id: CurrentUserId, settings: AppSettings):
    """Create a Lemon Squeezy hosted checkout for a subscription plan."""
    if not settings.lemonsqueezy_api_key:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Lemon Squeezy not configured")

    variant_id = settings.plan_variant_id(req.plan_id)
    if not variant_id:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=f"No variant configured for {req.plan_id}")

    url = await lemonsqueezy.create_checkout(variant_id, user_id, req.success_url)
    if not url:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Failed to create checkout")
    return CheckoutResponse(url=url)


@router.get("/portal", response_model=PortalResponse)
async def get_customer_portal(user_id: CurrentUserId, settings: AppSettings):
    """Return the Lemon Squeezy customer portal URL for the user's subscription."""
    if not settings.lemonsqueezy_api_key:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Lemon Squeezy not configured")

    client = get_supabase_client()
    sub = (
        client.table("subscriptions")
        .select("lemonsqueezy_subscription_id")
        .eq("user_id", user_id)
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    if not sub.data or not sub.data[0].get("lemonsqueezy_subscription_id"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No active subscription")

    url = await lemonsqueezy.get_subscription_portal_url(sub.data[0]["lemonsqueezy_subscription_id"])
    if not url:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Failed to load customer portal")
    return PortalResponse(url=url)


# Subscription statuses that grant plan access. Anything else (expired, unpaid)
# drops the user back to the free tier.
_ACTIVE_STATUSES = {"active", "on_trial", "past_due", "cancelled", "paused"}


def _provision_quota(client, user_id: str, plan_id: str, renews_at, plan_changed: bool):
    """Set the user's plan and provision their monthly VIDEO quota on the credits
    row. On a new subscription or a plan change we refill the balance to the new
    allotment; otherwise we leave the running balance and let the monthly reset
    (reset_date) refill it — so benign subscription_updated events never top up."""
    from app.core.config import PLANS
    plan = PLANS.get(plan_id, PLANS["free"])

    client.table("users").update({"plan_tier": plan_id}).eq("id", user_id).execute()

    updates: dict = {"monthly_quota": plan.videos_per_month}
    if plan_id == "free":
        # Cancelled/expired: no auto-refill; keep whatever balance remains.
        updates["reset_date"] = None
    else:
        updates["reset_date"] = renews_at
        if plan_changed:
            updates["balance"] = plan.videos_per_month
    client.table("credits").update(updates).eq("user_id", user_id).execute()


@router.post("/webhook")
async def lemonsqueezy_webhook(request: Request, settings: AppSettings):
    """Handle Lemon Squeezy webhooks for subscription events."""
    if not settings.lemonsqueezy_webhook_secret:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Lemon Squeezy not configured")

    payload = await request.body()
    signature = request.headers.get("X-Signature", "")
    if not lemonsqueezy.verify_signature(payload, signature):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid webhook signature")

    event = json.loads(payload)
    meta = event.get("meta", {})
    event_name = meta.get("event_name", "")
    custom = meta.get("custom_data", {}) or {}
    user_id = custom.get("user_id")
    if not user_id:
        return {"received": True}

    attrs = event["data"]["attributes"]
    client = get_supabase_client()

    if event_name.startswith("subscription_"):
        sub_id = event["data"]["id"]
        sub_status = attrs.get("status", "")
        plan_id = lemonsqueezy.resolve_plan_tier(attrs.get("variant_id")) if sub_status in _ACTIVE_STATUSES else "free"

        # Detect a plan change (incl. first activation) to decide whether to
        # refill the video balance vs. leave it for the monthly reset.
        current = (
            client.table("users").select("plan_tier").eq("id", user_id).single().execute().data
        )
        plan_changed = (current or {}).get("plan_tier") != plan_id
        renews_at = attrs.get("renews_at") or attrs.get("ends_at")

        _provision_quota(client, user_id, plan_id, renews_at, plan_changed)
        client.table("subscriptions").upsert({
            "user_id": user_id,
            "lemonsqueezy_subscription_id": str(sub_id),
            "plan_tier": plan_id,
            "status": sub_status,
            "current_period_start": attrs.get("created_at"),
            "current_period_end": attrs.get("ends_at") or attrs.get("renews_at"),
        }, on_conflict="lemonsqueezy_subscription_id").execute()

    return {"received": True}
