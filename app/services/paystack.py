"""Paystack billing (replaced Lemon Squeezy, 2026-08-05).

Faith's Paystack account settles in NGN and cannot charge USD, so every amount
here is Naira. Paystack works in kobo (1/100 of a Naira), so prices are
multiplied on the way out and divided on the way in.

Two purchase shapes:
  * subscriptions — initialize a transaction against a plan code; Paystack then
    bills the saved card each month and sends `subscription.*` / `invoice.*`.
  * one-off top-ups — initialize a plain transaction; the credits are granted
    once when `charge.success` arrives.

Both carry our `user_id` in `metadata` so the webhook can match the payer.
"""

import hashlib
import hmac

import httpx

from app.core.config import get_settings

settings = get_settings()
PAYSTACK_BASE = "https://api.paystack.co"


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {settings.paystack_secret_key}",
        "Content-Type": "application/json",
    }


def verify_signature(payload: bytes, signature: str) -> bool:
    """Validate a webhook against the secret key.

    Paystack signs the raw body with HMAC-SHA512 keyed on the SECRET KEY itself
    (there is no separate webhook secret) and sends the hex digest in
    x-paystack-signature.
    """
    if not settings.paystack_secret_key or not signature:
        return False
    expected = hmac.new(
        settings.paystack_secret_key.encode(),
        payload,
        hashlib.sha512,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


async def initialize_transaction(
    email: str,
    amount_ngn: int,
    *,
    metadata: dict,
    plan_code: str | None = None,
    callback_url: str | None = None,
) -> str | None:
    """Start a checkout and return the hosted payment URL (None on failure).

    Passing `plan_code` turns the charge into a subscription: Paystack ignores
    the amount and uses the plan's price, then renews it automatically.
    """
    body: dict = {
        "email": email,
        "amount": int(amount_ngn) * 100,   # Paystack bills in kobo
        "currency": "NGN",
        "metadata": {k: str(v) for k, v in metadata.items()},
    }
    if plan_code:
        body["plan"] = plan_code
    if callback_url:
        body["callback_url"] = callback_url

    async with httpx.AsyncClient(timeout=30) as client:
        try:
            resp = await client.post(
                f"{PAYSTACK_BASE}/transaction/initialize", headers=_headers(), json=body
            )
            if resp.status_code in (200, 201):
                return resp.json()["data"]["authorization_url"]
            return None
        except (httpx.HTTPError, KeyError):
            return None


async def subscription_manage_link(subscription_code: str) -> str | None:
    """A Paystack-hosted page where the customer can update their card or cancel."""
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            resp = await client.get(
                f"{PAYSTACK_BASE}/subscription/{subscription_code}/manage/link",
                headers=_headers(),
            )
            if resp.status_code == 200:
                return resp.json()["data"]["link"]
            return None
        except (httpx.HTTPError, KeyError):
            return None


async def disable_subscription(subscription_code: str, email_token: str) -> bool:
    """Cancel a subscription. Paystack requires the token from the subscription."""
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            resp = await client.post(
                f"{PAYSTACK_BASE}/subscription/disable",
                headers=_headers(),
                json={"code": subscription_code, "token": email_token},
            )
            return resp.status_code == 200
        except httpx.HTTPError:
            return False


def resolve_plan_tier(plan_code: str) -> str | None:
    """Map a Paystack plan code back to our internal plan id (see PLANS)."""
    return settings.plan_for_code(plan_code)
