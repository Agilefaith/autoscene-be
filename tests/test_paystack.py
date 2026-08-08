"""Paystack billing: webhook signing and the NGN/kobo conversion.

The webhook is the only path that grants credits, so an unsigned or tampered
body must never be accepted.
"""

import hashlib
import hmac
import json

from app.core.config import PLANS, TOPUP_PACKS, get_settings
from app.services import paystack

settings = get_settings()


def _sign(payload: bytes) -> str:
    return hmac.new(settings.paystack_secret_key.encode(), payload, hashlib.sha512).hexdigest()


def test_a_correctly_signed_payload_is_accepted():
    payload = json.dumps({"event": "charge.success"}).encode()
    assert paystack.verify_signature(payload, _sign(payload)) is True


def test_a_tampered_body_is_rejected():
    payload = json.dumps({"event": "charge.success", "data": {"amount": 100}}).encode()
    signature = _sign(payload)
    tampered = json.dumps({"event": "charge.success", "data": {"amount": 999999}}).encode()
    assert paystack.verify_signature(tampered, signature) is False


def test_an_unsigned_request_is_rejected():
    payload = json.dumps({"event": "charge.success"}).encode()
    assert paystack.verify_signature(payload, "") is False
    assert paystack.verify_signature(payload, "deadbeef") is False


def test_plan_codes_round_trip_to_plan_ids():
    """The webhook maps a Paystack plan code back to our plan id; if that mapping
    breaks, a paying customer lands on no plan at all."""
    for plan_id in PLANS:
        code = settings.plan_code(plan_id)
        if code:  # only plans that have been created in Paystack
            assert paystack.resolve_plan_tier(code) == plan_id


def test_an_unknown_plan_code_resolves_to_nothing():
    assert paystack.resolve_plan_tier("PLN_not_ours") is None
    assert paystack.resolve_plan_tier("") is None


def test_prices_are_whole_naira_so_kobo_conversion_is_exact():
    # initialize_transaction multiplies by 100; a fractional Naira price would
    # silently round and charge the wrong amount.
    for plan in PLANS.values():
        assert plan.price_ngn == int(plan.price_ngn)
    for pack in TOPUP_PACKS.values():
        assert pack.price_ngn == int(pack.price_ngn)
