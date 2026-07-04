import hashlib
import hmac

import httpx

from app.core.config import get_settings

settings = get_settings()
LS_BASE = "https://api.lemonsqueezy.com/v1"


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {settings.lemonsqueezy_api_key}",
        "Accept": "application/vnd.api+json",
        "Content-Type": "application/vnd.api+json",
    }


def verify_signature(payload: bytes, signature: str) -> bool:
    """Validate a webhook request against the configured signing secret.

    Lemon Squeezy signs each webhook with HMAC-SHA256 over the raw body and
    sends the hex digest in the X-Signature header.
    """
    if not settings.lemonsqueezy_webhook_secret or not signature:
        return False
    expected = hmac.new(
        settings.lemonsqueezy_webhook_secret.encode(),
        payload,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


def resolve_plan_tier(variant_id: str | int) -> str:
    """Map a Lemon Squeezy variant id to our internal plan id (see PLANS)."""
    return settings.plan_for_variant(variant_id)


async def create_checkout(
    variant_id: str,
    user_id: str,
    redirect_url: str | None = None,
    custom: dict | None = None,
) -> str | None:
    """Create a hosted checkout for a variant (subscription or one-time pack).

    The user_id (plus any extra `custom` keys) is attached as custom data so it
    comes back in webhook events (meta.custom_data), letting us match the buyer
    to our local user and know what to grant. Returns the checkout URL, or None.
    """
    custom_data = {"user_id": str(user_id)}
    if custom:
        custom_data.update({k: str(v) for k, v in custom.items()})

    attributes: dict = {
        "checkout_data": {"custom": custom_data},
    }
    if redirect_url:
        attributes["product_options"] = {"redirect_url": redirect_url}

    body = {
        "data": {
            "type": "checkouts",
            "attributes": attributes,
            "relationships": {
                "store": {
                    "data": {"type": "stores", "id": str(settings.lemonsqueezy_store_id)}
                },
                "variant": {
                    "data": {"type": "variants", "id": str(variant_id)}
                },
            },
        }
    }

    async with httpx.AsyncClient(timeout=30) as client:
        try:
            resp = await client.post(f"{LS_BASE}/checkouts", headers=_headers(), json=body)
            if resp.status_code in (200, 201):
                return resp.json()["data"]["attributes"]["url"]
            return None
        except (httpx.HTTPError, KeyError):
            return None


async def get_subscription_portal_url(subscription_id: str) -> str | None:
    """Fetch the Lemon Squeezy customer portal URL for a subscription.

    The portal lets the customer update payment method, pause, or cancel.
    Returns the signed portal URL, or None on failure.
    """
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            resp = await client.get(
                f"{LS_BASE}/subscriptions/{subscription_id}", headers=_headers()
            )
            if resp.status_code == 200:
                return resp.json()["data"]["attributes"]["urls"]["customer_portal"]
            return None
        except (httpx.HTTPError, KeyError):
            return None
