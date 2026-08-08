from postgrest.exceptions import APIError

from app.services.supabase import get_supabase_client
from app.core.config import get_settings

settings = get_settings()

# Postgres SQLSTATE for a plpgsql RAISE EXCEPTION with no explicit code — what
# the billing functions use to refuse a charge.
_PG_RAISE_EXCEPTION = "P0001"


def calculate_project_credits(duration_seconds: int) -> int:
    """Credits a render costs: one per started minute of video (config-driven).

    Faith, 2026-08-05: billing is in minutes, so a 20-minute video costs 20 of a
    plan's credits. There is no render-mode multiplier any more — Mode 1 is the
    only mode.
    """
    return settings.credits_for(duration_seconds)


def consume_credits(user_id: str, project_id: str, request_id: str, credits: int) -> dict:
    """Atomically charge a render's credits and flip an existing draft project to
    'pending'. Applies the no-rollover monthly reset, spends the plan allowance
    before purchased (non-expiring) credits, and raises ValueError when the
    combined balance can't cover the cost.
    Uses the consume_credits_atomic stored procedure for atomicity."""
    client = get_supabase_client()
    try:
        result = client.rpc(
            "consume_credits_atomic",
            {
                "p_user_id": user_id,
                "p_project_id": project_id,
                "p_request_id": request_id,
                "p_credits": credits,
            },
        ).execute()
    except APIError as e:
        # The function refuses the charge with RAISE EXCEPTION, which PostgREST
        # surfaces as an APIError rather than an empty result. Translating it
        # here is what turns "not enough credits" into a 402 for the user
        # instead of a 500 (see api/routes/projects.py).
        if (e.code or "") == _PG_RAISE_EXCEPTION:
            raise ValueError(e.message or "Insufficient credits") from e
        raise
    if not result.data:
        raise ValueError("Insufficient credits or project render start failed")
    return result.data


def refund_credits(user_id: str, project_id: str) -> None:
    """Refund a failed render, returning each part to where it was charged from.

    Credits spent out of a purchased (non-expiring) top-up go back to the top-up
    balance rather than the monthly allowance, which the next reset would wipe.
    The amount comes from the project row, so this is safe to call more than once.
    """
    client = get_supabase_client()
    client.rpc(
        "refund_project_credits",
        {
            "p_user_id": user_id,
            "p_project_id": project_id,
        },
    ).execute()
