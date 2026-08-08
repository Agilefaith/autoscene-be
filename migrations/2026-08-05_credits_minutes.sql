-- ─────────────────────────────────────────────────────────────────────────────
-- Credit billing in minutes + non-expiring top-ups (Faith, 2026-08-05)
--
-- Billing moves from "1 video = 1 quota unit" (2026-07-04_plan_quota.sql) to
-- CREDITS, where one credit is one minute of finished video. A 20-minute video
-- costs 20 credits, so the plan allowance itself caps how much can be rendered
-- and duration no longer needs a separate per-plan gate.
--
--   credits.balance        monthly plan allowance — resets each period, NO rollover
--   credits.topup_balance  Pay-As-You-Go credits — bought separately, never expire
--
-- Consumption always spends the monthly allowance first and only then dips into
-- purchased credits, so a top-up is never wasted by the monthly reset.
-- ─────────────────────────────────────────────────────────────────────────────

ALTER TABLE public.credits
  ADD COLUMN IF NOT EXISTS topup_balance INTEGER NOT NULL DEFAULT 0;

-- How much of this render was paid for out of purchased credits. Recorded so a
-- refund can return each part to where it came from: without it, credits taken
-- from a non-expiring top-up would be refunded into the monthly allowance and
-- then wiped by the next reset.
ALTER TABLE public.projects
  ADD COLUMN IF NOT EXISTS credits_from_topup INTEGER NOT NULL DEFAULT 0;


-- ── atomic: charge a render's credits + flip the draft to pending ─────────────
-- Replaces consume_video_quota_atomic (dropped below). Locks the credits row,
-- lazily rolls the period forward if it has ended (no rollover), verifies the
-- combined balance covers the cost, spends allowance-then-topup, logs the
-- transaction and stamps the project — all in one transaction.
CREATE OR REPLACE FUNCTION public.consume_credits_atomic(
  p_user_id     UUID,
  p_project_id  UUID,
  p_request_id  TEXT,
  p_credits     INTEGER
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
  v_balance INTEGER;
  v_topup   INTEGER;
  v_quota   INTEGER;
  v_reset   TIMESTAMPTZ;
  v_from_plan  INTEGER;
  v_from_topup INTEGER;
BEGIN
  IF p_credits IS NULL OR p_credits < 1 THEN
    RAISE EXCEPTION 'Credit amount must be at least 1';
  END IF;

  SELECT balance, topup_balance, monthly_quota, reset_date
    INTO v_balance, v_topup, v_quota, v_reset
  FROM public.credits
  WHERE user_id = p_user_id
  FOR UPDATE;

  IF NOT FOUND THEN
    RAISE EXCEPTION 'No credit record for this user';
  END IF;

  -- Lazy monthly reset: refill the allowance and advance the date one month at
  -- a time until it is in the future. Purchased credits are untouched.
  IF v_reset IS NOT NULL AND v_quota IS NOT NULL THEN
    WHILE v_reset <= now() LOOP
      v_balance := v_quota;
      v_reset   := v_reset + INTERVAL '1 month';
    END LOOP;
    UPDATE public.credits
    SET balance = v_balance, reset_date = v_reset
    WHERE user_id = p_user_id;
  END IF;

  v_balance := COALESCE(v_balance, 0);
  v_topup   := COALESCE(v_topup, 0);

  IF (v_balance + v_topup) < p_credits THEN
    RAISE EXCEPTION 'Insufficient credits: need %, have %', p_credits, v_balance + v_topup;
  END IF;

  -- Spend the monthly allowance first, then purchased credits.
  v_from_plan  := LEAST(v_balance, p_credits);
  v_from_topup := p_credits - v_from_plan;

  UPDATE public.credits
  SET balance       = COALESCE(balance, 0) - v_from_plan,
      topup_balance = COALESCE(topup_balance, 0) - v_from_topup
  WHERE user_id = p_user_id;

  -- credit_transactions.video_job_id has no FK, so it holds project ids too
  -- (same as the other billing functions).
  INSERT INTO public.credit_transactions (user_id, amount, type, video_job_id)
  VALUES (p_user_id, -p_credits, 'deduction', p_project_id);

  UPDATE public.projects
  SET status             = 'pending',
      credits_used       = p_credits,
      credits_from_topup = v_from_topup,
      request_id         = p_request_id,
      error_message      = NULL
  WHERE id = p_project_id AND user_id = p_user_id;

  RETURN jsonb_build_object(
    'project_id',        p_project_id,
    'credits',           p_credits,
    'from_plan',         v_from_plan,
    'from_topup',        v_from_topup,
    'credits_remaining', (v_balance - v_from_plan) + (v_topup - v_from_topup)
  );
END;
$$;


-- ── add purchased (non-expiring) credits after a successful PAYG charge ───────
-- Idempotent on the Paystack transaction reference: the insert into
-- credit_transactions.external_ref is covered by a unique index (see
-- 2026-06-18_stripe_to_lemonsqueezy.sql), so a webhook Paystack re-delivers
-- raises unique_violation and grants nothing the second time.
CREATE OR REPLACE FUNCTION public.add_topup_credits(
  p_user_id   UUID,
  p_credits   INTEGER,
  p_reference TEXT
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
  v_topup INTEGER;
BEGIN
  INSERT INTO public.credit_transactions (user_id, amount, type, external_ref)
  VALUES (p_user_id, p_credits, 'topup', p_reference);

  INSERT INTO public.credits (user_id, balance, topup_balance)
  VALUES (p_user_id, 0, p_credits)
  ON CONFLICT (user_id) DO UPDATE
    SET topup_balance = COALESCE(public.credits.topup_balance, 0) + p_credits
  RETURNING topup_balance INTO v_topup;

  RETURN jsonb_build_object('user_id', p_user_id, 'topup_balance', v_topup,
                            'duplicate', false);
EXCEPTION WHEN unique_violation THEN
  SELECT COALESCE(topup_balance, 0) INTO v_topup
  FROM public.credits WHERE user_id = p_user_id;
  RETURN jsonb_build_object('user_id', p_user_id, 'topup_balance', v_topup,
                            'duplicate', true);
END;
$$;


-- ── put a user on a plan (subscription started/renewed) ───────────────────────
-- Refills the monthly allowance to the plan's credits and sets the next reset a
-- month out. Purchased credits are left alone.
CREATE OR REPLACE FUNCTION public.apply_plan_credits(
  p_user_id UUID,
  p_plan_id TEXT,
  p_credits INTEGER
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
BEGIN
  INSERT INTO public.credits (user_id, balance, monthly_quota, reset_date)
  VALUES (p_user_id, p_credits, p_credits, now() + INTERVAL '1 month')
  ON CONFLICT (user_id) DO UPDATE
    SET balance       = p_credits,
        monthly_quota = p_credits,
        reset_date    = now() + INTERVAL '1 month';

  -- Internal accounts are never billed, so a subscription must not demote one.
  UPDATE public.users
  SET plan_tier = p_plan_id,
      user_type = CASE WHEN user_type = 'internal' THEN 'internal' ELSE 'standard' END
  WHERE id = p_user_id;

  RETURN jsonb_build_object('user_id', p_user_id, 'plan_id', p_plan_id,
                            'credits', p_credits);
END;
$$;


-- ── refund a failed render, each part back to where it was taken from ────────
-- The old refund_credits() put everything into `balance`, which would silently
-- convert purchased (non-expiring) credits into monthly allowance that the next
-- reset wipes. This reads the split recorded at charge time instead.
CREATE OR REPLACE FUNCTION public.refund_project_credits(
  p_user_id    UUID,
  p_project_id UUID
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
  v_used  INTEGER;
  v_topup INTEGER;
BEGIN
  SELECT COALESCE(credits_used, 0), COALESCE(credits_from_topup, 0)
    INTO v_used, v_topup
  FROM public.projects
  WHERE id = p_project_id AND user_id = p_user_id
  FOR UPDATE;

  IF NOT FOUND OR v_used < 1 THEN
    RETURN jsonb_build_object('refunded', 0);
  END IF;

  UPDATE public.credits
  SET balance       = COALESCE(balance, 0) + (v_used - v_topup),
      topup_balance = COALESCE(topup_balance, 0) + v_topup
  WHERE user_id = p_user_id;

  INSERT INTO public.credit_transactions (user_id, amount, type, video_job_id)
  VALUES (p_user_id, v_used, 'refund', p_project_id);

  -- Zeroed so a retry of the same project cannot be refunded twice.
  UPDATE public.projects
  SET credits_used = 0, credits_from_topup = 0
  WHERE id = p_project_id;

  RETURN jsonb_build_object('refunded', v_used, 'to_topup', v_topup);
END;
$$;


-- ── Lemon Squeezy → Paystack ─────────────────────────────────────────────────
-- Faith's Paystack account settles in NGN; Lemon Squeezy is gone entirely.
-- Cancelling a Paystack subscription needs BOTH its code and its email token,
-- so the token is stored alongside.
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.columns
             WHERE table_schema = 'public' AND table_name = 'subscriptions'
               AND column_name = 'lemonsqueezy_subscription_id') THEN
    ALTER TABLE public.subscriptions
      RENAME COLUMN lemonsqueezy_subscription_id TO paystack_subscription_code;
  END IF;
  IF EXISTS (SELECT 1 FROM information_schema.columns
             WHERE table_schema = 'public' AND table_name = 'users'
               AND column_name = 'lemonsqueezy_customer_id') THEN
    ALTER TABLE public.users
      RENAME COLUMN lemonsqueezy_customer_id TO paystack_customer_code;
  END IF;
END $$;

ALTER TABLE public.subscriptions
  ADD COLUMN IF NOT EXISTS paystack_email_token TEXT;


-- With the free tier gone, "no plan" is a real state: an account whose
-- subscription lapsed keeps its login and its purchased credits but has no
-- monthly allowance. The column was NOT NULL because everyone used to fall back
-- to 'free'.
ALTER TABLE public.users ALTER COLUMN plan_tier DROP NOT NULL;
ALTER TABLE public.users ALTER COLUMN plan_tier DROP DEFAULT;


-- ── move existing accounts onto the new catalog ──────────────────────────────
-- The old ids (free, creator_m2, scale_m2) no longer exist. Mode 2 plans collapse
-- onto their Mode 1 equivalent since Mode 2 is gone, and the free tier maps to no
-- plan at all. Balances were counted in VIDEOS, which is meaningless now, so each
-- account is reset to its plan's credit allowance.
UPDATE public.users SET plan_tier = 'creator' WHERE plan_tier = 'creator_m2';
UPDATE public.users SET plan_tier = 'scale'   WHERE plan_tier = 'scale_m2';
UPDATE public.users SET plan_tier = NULL      WHERE plan_tier = 'free';
UPDATE public.users SET plan_tier = NULL
 WHERE plan_tier IS NOT NULL
   AND plan_tier NOT IN ('starter', 'creator', 'pro', 'scale');

UPDATE public.credits c
SET balance = v.credits, monthly_quota = v.credits
FROM public.users u
JOIN (VALUES ('starter', 20), ('creator', 60), ('pro', 150), ('scale', 350))
     AS v(plan_id, credits) ON v.plan_id = u.plan_tier
WHERE c.user_id = u.id;

-- No subscription means no monthly refill; purchased credits are left alone.
UPDATE public.credits c
SET balance = 0, monthly_quota = 0, reset_date = NULL
FROM public.users u
WHERE c.user_id = u.id AND u.plan_tier IS NULL AND u.user_type <> 'internal';


-- The per-video quota model is gone; drop its entry point so nothing can call it.
DROP FUNCTION IF EXISTS public.consume_video_quota_atomic(UUID, UUID, TEXT);
DROP FUNCTION IF EXISTS public.start_project_render_atomic(UUID, UUID, INTEGER, TEXT);
