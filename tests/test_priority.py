"""
Production-readiness tests for tier-based queue priority.

Two layers:
1. Config tests (always run, no broker needed) — guarantee the priority settings
   actually ship in code, not just on someone's local box.
2. A real Redis broker test (skipped if Redis is unavailable) — proves the
   *direction*: paid jobs (priority 0) are drained before free jobs (priority 9).
"""
import pytest
from app.core.celery_app import celery_app
from app.api.routes.projects import PRIORITY_PAID, PRIORITY_FREE


# ── 1. Config (CI-safe, no broker) ────────────────────────────────────────────

def test_celery_priority_config_present():
    """Redis ignores priority unless these ship — lock them in CI."""
    opts = celery_app.conf.broker_transport_options or {}
    assert opts.get("queue_order_strategy") == "priority"
    # buckets must cover the values we publish with
    steps = opts.get("priority_steps") or []
    assert PRIORITY_PAID in steps and PRIORITY_FREE in steps
    # prefetch=1 is mandatory or a worker buffers tasks and ignores priority
    assert celery_app.conf.worker_prefetch_multiplier == 1


def test_priority_constants_direction():
    """On Redis, 0 = highest priority. Paid must be the lower number."""
    assert PRIORITY_PAID == 0
    assert PRIORITY_PAID < PRIORITY_FREE


# ── 2. Real broker behaviour (skipped without Redis) ──────────────────────────

@pytest.fixture
def redis_client():
    redis = pytest.importorskip("redis")
    from app.core.config import get_settings
    try:
        r = redis.from_url(get_settings().redis_url)
        r.ping()
    except Exception:
        pytest.skip("Redis not available")
    return r


def test_redis_buckets_paid_ahead_of_free(redis_client):
    """Publish a paid + a free task; confirm the worker's BRPOP scan order
    (priority_steps, left→right) reaches the paid task first."""
    r = redis_client
    Q = "prioritytest_ci"
    suffixes = [""] + [f":{i}" for i in range(1, 10)]
    for suf in suffixes:
        r.delete(f"{Q}{suf}")

    try:
        celery_app.send_task("dummy.noop", args=[], queue=Q, priority=PRIORITY_PAID)
        celery_app.send_task("dummy.noop", args=[], queue=Q, priority=PRIORITY_FREE)

        # paid (0) lands in the base key; free (9) in the ":9" bucket
        assert r.llen(Q) == 1, "paid task should be in the base (highest) bucket"
        assert r.llen(f"{Q}:9") == 1, "free task should be in the lowest bucket"

        # worker scans steps 0..9 left→right and pops the first non-empty → paid
        steps = celery_app.conf.broker_transport_options["priority_steps"]
        first_non_empty = next(
            pri for pri in steps
            if r.llen(Q if pri == 0 else f"{Q}:{pri}")
        )
        assert first_non_empty == PRIORITY_PAID
    finally:
        for suf in suffixes:
            r.delete(f"{Q}{suf}")
