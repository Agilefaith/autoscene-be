-- ─────────────────────────────────────────────────────────────────────────────
-- AutoScene plan/quota billing (Faith-aligned)
--
-- Billing moves from a credits-per-second balance to a per-month VIDEO QUOTA:
--   credits.balance       = videos remaining THIS period
--   credits.monthly_quota = the plan's monthly allotment
--   credits.reset_date    = when the current allotment refills (next period)
-- Each generated video costs 1 unit. Duration + render mode are gated per plan
-- in the API layer. No rollover: on reset, balance is set to monthly_quota
-- (never accumulated). Free/trial rows have reset_date = NULL (no auto-refill).
-- ─────────────────────────────────────────────────────────────────────────────

-- ── atomic: consume 1 video from the monthly quota + flip a draft to pending ──
-- Locks the credits row, lazily rolls the period forward if it has ended (no
-- rollover), verifies at least 1 video remains, decrements, logs the txn, and
-- stamps the project — all in one transaction. Mirrors start_project_render_atomic.
CREATE OR REPLACE FUNCTION public.consume_video_quota_atomic(
  p_user_id     UUID,
  p_project_id  UUID,
  p_request_id  TEXT
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
  v_balance INTEGER;
  v_quota   INTEGER;
  v_reset   TIMESTAMPTZ;
BEGIN
  SELECT balance, monthly_quota, reset_date
    INTO v_balance, v_quota, v_reset
  FROM public.credits
  WHERE user_id = p_user_id
  FOR UPDATE;

  -- Lazy monthly reset (no rollover): if the period has ended, refill to the
  -- plan allotment and advance the reset date one month at a time until future.
  IF v_reset IS NOT NULL AND v_quota IS NOT NULL THEN
    WHILE v_reset <= now() LOOP
      v_balance := v_quota;
      v_reset   := v_reset + INTERVAL '1 month';
    END LOOP;
    UPDATE public.credits
    SET balance = v_balance, reset_date = v_reset
    WHERE user_id = p_user_id;
  END IF;

  IF v_balance < 1 THEN
    RAISE EXCEPTION 'Video quota exhausted for this period';
  END IF;

  UPDATE public.credits
  SET balance = balance - 1
  WHERE user_id = p_user_id;

  INSERT INTO public.credit_transactions (user_id, amount, type, video_job_id)
  VALUES (p_user_id, -1, 'deduction', p_project_id);

  UPDATE public.projects
  SET status = 'pending',
      credits_used = 1,
      request_id = p_request_id,
      error_message = NULL
  WHERE id = p_project_id AND user_id = p_user_id;

  RETURN jsonb_build_object('project_id', p_project_id, 'videos_remaining', v_balance - 1);
END;
$$;

-- ── safety-net: refill any periods that have already ended ────────────────────
-- Called by a periodic Celery beat task so a user's displayed quota refreshes
-- even if they don't generate right at the boundary. Same no-rollover logic.
CREATE OR REPLACE FUNCTION public.reset_expired_quotas()
RETURNS INTEGER
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
  v_row   RECORD;
  v_reset TIMESTAMPTZ;
  v_count INTEGER := 0;
BEGIN
  FOR v_row IN
    SELECT user_id, monthly_quota, reset_date
    FROM public.credits
    WHERE reset_date IS NOT NULL
      AND monthly_quota IS NOT NULL
      AND reset_date <= now()
    FOR UPDATE
  LOOP
    v_reset := v_row.reset_date;
    WHILE v_reset <= now() LOOP
      v_reset := v_reset + INTERVAL '1 month';
    END LOOP;
    UPDATE public.credits
    SET balance = v_row.monthly_quota, reset_date = v_reset
    WHERE user_id = v_row.user_id;
    v_count := v_count + 1;
  END LOOP;
  RETURN v_count;
END;
$$;
