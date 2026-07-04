#!/usr/bin/env python3
"""Send a correctly-signed fake Lemon Squeezy webhook to the local backend.

Lets you test the webhook handler end-to-end (signature check -> plan_tier
update) without a public tunnel or a real Lemon Squeezy purchase.

Usage:
    python scripts/test_webhook.py <user_id> [--plan pro|premium]
                                            [--event subscription_created]
                                            [--status active]
                                            [--url http://localhost:8000]

Reads LEMONSQUEEZY_* values from backend/.env (same dir resolution as the app).
"""
import argparse
import hashlib
import hmac
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"


def load_env(path: Path) -> dict:
    if not path.exists():
        sys.exit(f"Cannot find .env at {path}")
    env = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip()
    return env


def main() -> None:
    parser = argparse.ArgumentParser(description="Send a signed fake Lemon Squeezy webhook.")
    parser.add_argument("user_id", help="ZEL UGC user id (UUID) to update")
    parser.add_argument("--plan", choices=["pro", "premium"], default="pro")
    parser.add_argument("--event", default="subscription_created")
    parser.add_argument("--status", default="active", help="Subscription status (active, cancelled, expired, ...)")
    parser.add_argument("--url", default="http://localhost:8000")
    args = parser.parse_args()

    env = load_env(ENV_PATH)
    secret = env.get("LEMONSQUEEZY_WEBHOOK_SECRET")
    if not secret:
        sys.exit("LEMONSQUEEZY_WEBHOOK_SECRET missing in .env")

    variant_id = env.get(
        "LEMONSQUEEZY_PRO_VARIANT_ID" if args.plan == "pro" else "LEMONSQUEEZY_PREMIUM_VARIANT_ID"
    )
    now = datetime.now(timezone.utc).isoformat()

    payload = {
        "meta": {
            "event_name": args.event,
            "custom_data": {"user_id": args.user_id},
        },
        "data": {
            "type": "subscriptions",
            "id": "999999",  # fake subscription id
            "attributes": {
                "store_id": int(env.get("LEMONSQUEEZY_STORE_ID", 0)),
                "variant_id": int(variant_id) if variant_id else None,
                "status": args.status,
                "created_at": now,
                "renews_at": now,
                "ends_at": None,
            },
        },
    }

    body = json.dumps(payload).encode()
    signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    endpoint = f"{args.url.rstrip('/')}/api/billing/webhook"
    print(f"POST {endpoint}")
    print(f"  event={args.event} plan={args.plan} variant={variant_id} status={args.status} user={args.user_id}")

    resp = httpx.post(
        endpoint,
        content=body,
        headers={"Content-Type": "application/json", "X-Signature": signature},
        timeout=30,
    )
    print(f"-> {resp.status_code} {resp.text}")


if __name__ == "__main__":
    main()
