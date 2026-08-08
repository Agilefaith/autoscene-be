import json

from fastapi import APIRouter, Request, HTTPException, status
from app.core.config import PLANS, TOPUP_PACKS
from app.core.deps import CurrentUserId, CurrentUser, AppSettings
from app.schemas.billing import (
    BillingUsageResponse,
    CheckoutRequest,
    CheckoutResponse,
    CreditTransactionResponse,
    PlanResponse,
    PortalResponse,
    TopupPackResponse,
    TopupRequest,
)
from app.services import paystack
from app.services.supabase import get_supabase_client

router = APIRouter(prefix="/billing", tags=["billing"])


@router.get("/plans")
async def list_plans(_user_id: CurrentUserId):
    """The plan catalog and top-up packs, for the pricing/billing screens.

    One credit is one minute of finished video, so `credits_per_month` doubles as
    "minutes of video per month".
    """
    return {
        "currency": "NGN",
        "plans": [
            PlanResponse(id=p.id, name=p.name, price_ngn=p.price_ngn,
                         credits_per_month=p.credits_per_month)
            for p in PLANS.values()
        ],
        "topup_packs": [
            TopupPackResponse(id=t.id, credits=t.credits, price_ngn=t.price_ngn)
            for t in TOPUP_PACKS.values()
        ],
    }


@router.get("/usage", response_model=BillingUsageResponse)
async def get_billing_usage(user_id: CurrentUserId, user: CurrentUser):
    client = get_supabase_client()

    credit_row = (
        client.table("credits")
        .select("balance, topup_balance, monthly_quota, reset_date")
        .eq("user_id", user_id)
        .single()
        .execute()
    )
    credits = credit_row.data or {}

    video_count = (
        client.table("projects")
        .select("id", count="exact")  # type: ignore[arg-type]
        .eq("user_id", user_id)
        .eq("status", "completed")
        .execute()
    )

    balance = credits.get("balance") or 0
    topup = credits.get("topup_balance") or 0
    return BillingUsageResponse(
        credit_balance=balance + topup,
        plan_credits=balance,
        topup_credits=topup,
        monthly_quota=credits.get("monthly_quota"),
        plan_tier=user.get("plan_tier") or "",
        user_type=user.get("user_type", "standard"),
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


def _billing_email(user: dict) -> str:
    email = (user or {}).get("email")
    if not email:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Your account has no email address on file.")
    return str(email)


@router.post("/checkout", response_model=CheckoutResponse)
async def create_checkout(
    req: CheckoutRequest, user_id: CurrentUserId, user: CurrentUser, settings: AppSettings
):
    """Start a Paystack subscription checkout for a monthly plan."""
    if not settings.paystack_secret_key:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="Payments are not configured yet.")
    plan = PLANS.get(req.plan_id)
    if not plan:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail=f"Unknown plan '{req.plan_id}'.")
    plan_code = settings.plan_code(req.plan_id)
    if not plan_code:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail=f"The {plan.name} plan is not available for purchase yet.")

    url = await paystack.initialize_transaction(
        _billing_email(user),
        plan.price_ngn,
        metadata={"user_id": user_id, "plan_id": plan.id, "kind": "subscription"},
        plan_code=plan_code,
        callback_url=req.success_url or settings.paystack_callback_url or None,
    )
    if not url:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY,
                            detail="Could not start the checkout. Please try again.")
    return CheckoutResponse(url=url)


@router.post("/topup", response_model=CheckoutResponse)
async def create_topup(
    req: TopupRequest, user_id: CurrentUserId, user: CurrentUser, settings: AppSettings
):
    """Start a one-off Pay-As-You-Go credit purchase. These credits never expire."""
    if not settings.paystack_secret_key:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="Payments are not configured yet.")
    pack = TOPUP_PACKS.get(req.pack_id)
    if not pack:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail=f"Unknown top-up pack '{req.pack_id}'.")

    url = await paystack.initialize_transaction(
        _billing_email(user),
        pack.price_ngn,
        metadata={"user_id": user_id, "pack_id": pack.id,
                  "credits": pack.credits, "kind": "topup"},
        callback_url=req.success_url or settings.paystack_callback_url or None,
    )
    if not url:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY,
                            detail="Could not start the checkout. Please try again.")
    return CheckoutResponse(url=url)


@router.get("/portal", response_model=PortalResponse)
async def get_customer_portal(user_id: CurrentUserId, settings: AppSettings):
    """A Paystack-hosted page where the customer updates their card or cancels."""
    if not settings.paystack_secret_key:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="Payments are not configured yet.")

    client = get_supabase_client()
    sub = (
        client.table("subscriptions")
        .select("paystack_subscription_code")
        .eq("user_id", user_id)
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    code = (sub.data or [{}])[0].get("paystack_subscription_code")
    if not code:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="You don't have an active subscription.")

    url = await paystack.subscription_manage_link(code)
    if not url:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY,
                            detail="Could not open the billing portal. Please try again.")
    return PortalResponse(url=url)


# Paystack subscription statuses that keep plan access. Anything else (completed,
# cancelled and past its period) drops the user off their plan.
_ACTIVE_STATUSES = {"active", "non-renewing", "attention"}


def _apply_plan(client, user_id: str, plan_id: str) -> None:
    """Put the user on a plan and refill their monthly credits to match it.

    Purchased top-up credits are deliberately untouched: they were paid for
    separately and never expire.
    """
    plan = PLANS.get(plan_id)
    if not plan:
        return
    client.rpc("apply_plan_credits", {
        "p_user_id": user_id,
        "p_plan_id": plan_id,
        "p_credits": plan.credits_per_month,
    }).execute()


def _drop_plan(client, user_id: str) -> None:
    """Subscription ended: stop the monthly refill but keep whatever is left."""
    client.table("users").update({"plan_tier": None}).eq("id", user_id).execute()
    client.table("credits").update(
        {"monthly_quota": 0, "reset_date": None}
    ).eq("user_id", user_id).execute()


@router.post("/webhook")
async def paystack_webhook(request: Request, settings: AppSettings):
    """Handle Paystack webhooks: subscription lifecycle and one-off top-ups.

    Paystack signs the raw body with HMAC-SHA512 keyed on the secret key, so an
    unsigned or mismatched request is rejected before anything is granted.
    """
    if not settings.paystack_secret_key:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="Payments are not configured yet.")

    payload = await request.body()
    signature = request.headers.get("x-paystack-signature", "")
    if not paystack.verify_signature(payload, signature):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Invalid webhook signature")

    event = json.loads(payload)
    event_name = event.get("event", "")
    data = event.get("data", {}) or {}
    metadata = data.get("metadata") or {}
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except ValueError:
            metadata = {}

    client = get_supabase_client()

    # ── one-off Pay-As-You-Go purchase ───────────────────────────────────────
    if event_name == "charge.success" and metadata.get("kind") == "topup":
        user_id = metadata.get("user_id")
        credits = int(metadata.get("credits") or 0)
        reference = data.get("reference")
        if user_id and credits > 0 and reference:
            # Idempotent on `reference`: a re-delivered webhook grants nothing.
            client.rpc("add_topup_credits", {
                "p_user_id": user_id,
                "p_credits": credits,
                "p_reference": str(reference),
            }).execute()
        return {"received": True}

    # ── subscription lifecycle ───────────────────────────────────────────────
    if event_name.startswith("subscription."):
        plan_code = (data.get("plan") or {}).get("plan_code", "")
        plan_id = paystack.resolve_plan_tier(plan_code)
        user_id = metadata.get("user_id") or _user_for_customer(client, data)
        if not user_id:
            return {"received": True}

        sub_status = data.get("status", "")
        sub_code = data.get("subscription_code")
        active = event_name != "subscription.not_renew" and sub_status in _ACTIVE_STATUSES

        if event_name == "subscription.disable" or not active:
            _drop_plan(client, user_id)
        elif plan_id:
            _apply_plan(client, user_id, plan_id)

        if sub_code:
            client.table("subscriptions").upsert({
                "user_id": user_id,
                "paystack_subscription_code": str(sub_code),
                "paystack_email_token": data.get("email_token"),
                "plan_tier": plan_id,
                "status": sub_status,
                "current_period_start": data.get("createdAt") or data.get("created_at"),
                "current_period_end": data.get("next_payment_date"),
            }, on_conflict="paystack_subscription_code").execute()
        return {"received": True}

    # ── monthly renewal succeeded → refill the allowance ─────────────────────
    if event_name in ("invoice.payment_succeeded", "invoice.update"):
        if (data.get("status") or "").lower() not in ("success", "paid", ""):
            return {"received": True}
        sub = data.get("subscription") or {}
        plan_id = paystack.resolve_plan_tier((data.get("plan") or {}).get("plan_code", ""))
        user_id = metadata.get("user_id") or _user_for_customer(client, data)
        if user_id and plan_id and sub.get("status") in _ACTIVE_STATUSES:
            _apply_plan(client, user_id, plan_id)

    return {"received": True}


def _user_for_customer(client, data: dict) -> str | None:
    """Fall back to matching the payer by Paystack customer code, then by email.

    Renewal events are raised by Paystack rather than by our checkout, so they do
    not always carry the metadata we attached at initialize time.
    """
    customer = data.get("customer") or {}
    code = customer.get("customer_code")
    if code:
        row = (
            client.table("users").select("id")
            .eq("paystack_customer_code", str(code)).limit(1).execute().data
        )
        if row:
            return row[0]["id"]
    email = (customer.get("email") or "").strip().lower()
    if email:
        row = (
            client.table("users").select("id").ilike("email", email).limit(1).execute().data
        )
        if row:
            return row[0]["id"]
    return None
