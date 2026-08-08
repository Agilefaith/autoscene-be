"""Create (or reconcile) the subscription plans on Paystack.

Paystack subscriptions are billed against a PLAN CODE that lives in the Paystack
account, so each plan in our catalog needs one created once. This script is
idempotent: a plan whose name already exists is updated in place rather than
duplicated, and it prints the env lines to paste into .env.

    cd backend && python -m scripts.paystack_setup          # create/update
    cd backend && python -m scripts.paystack_setup --dry-run
"""

import os
import sys

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import PLANS, get_settings  # noqa: E402

PAYSTACK_BASE = "https://api.paystack.co"
PLAN_PREFIX = "AutoScene "


def main() -> int:
    settings = get_settings()
    if not settings.paystack_secret_key:
        print("PAYSTACK_SECRET_KEY is not set — nothing to do.")
        return 1
    dry_run = "--dry-run" in sys.argv

    headers = {
        "Authorization": f"Bearer {settings.paystack_secret_key}",
        "Content-Type": "application/json",
    }

    with httpx.Client(timeout=30, headers=headers) as client:
        resp = client.get(f"{PAYSTACK_BASE}/plan", params={"perPage": 100})
        if resp.status_code != 200:
            print(f"Could not list plans: {resp.status_code} {resp.text[:200]}")
            return 1
        existing = {p["name"]: p for p in resp.json().get("data", [])}

        env_lines = []
        for plan in PLANS.values():
            name = f"{PLAN_PREFIX}{plan.name}"
            body = {
                "name": name,
                "amount": plan.price_ngn * 100,   # Paystack bills in kobo
                "interval": "monthly",
                "currency": "NGN",
                "description": f"{plan.credits_per_month} credits per month "
                               f"({plan.credits_per_month} minutes of video)",
            }
            current = existing.get(name)

            if dry_run:
                action = "update" if current else "create"
                print(f"[dry-run] would {action} {name}: NGN {plan.price_ngn:,}/mo")
                continue

            if current:
                r = client.put(f"{PAYSTACK_BASE}/plan/{current['plan_code']}", json=body)
                code = current["plan_code"]
                ok = r.status_code == 200
                verb = "updated"
            else:
                r = client.post(f"{PAYSTACK_BASE}/plan", json=body)
                ok = r.status_code in (200, 201)
                code = r.json().get("data", {}).get("plan_code", "") if ok else ""
                verb = "created"

            if not ok:
                print(f"FAILED {plan.id}: {r.status_code} {r.text[:200]}")
                return 1
            print(f"{verb} {name}: NGN {plan.price_ngn:,}/mo -> {code}")
            env_lines.append(f"{plan.plan_code_env.upper()}={code}")

    if env_lines:
        print("\nPaste into backend/.env:\n")
        print("\n".join(env_lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
