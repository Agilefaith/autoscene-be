ALTER TABLE public.users
  RENAME COLUMN stripe_customer_id TO lemonsqueezy_customer_id;

ALTER TABLE public.subscriptions
  RENAME COLUMN stripe_subscription_id TO lemonsqueezy_subscription_id;

-- ── Credit packs: idempotency support ─────────────────────────────────────────
-- external_ref keys a credit grant to a Lemon Squeezy order so re-delivered
-- webhooks don't double-credit.
ALTER TABLE public.credit_transactions
  ADD COLUMN IF NOT EXISTS external_ref TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS credit_tx_external_ref_unique
  ON public.credit_transactions(external_ref) WHERE external_ref IS NOT NULL;

CREATE OR REPLACE FUNCTION public.grant_credits_idempotent(
  p_user_id      UUID,
  p_credits      INTEGER,
  p_external_ref TEXT
)
RETURNS BOOLEAN
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
BEGIN
  INSERT INTO public.credit_transactions (user_id, amount, type, external_ref)
  VALUES (p_user_id, p_credits, 'purchase', p_external_ref);

  UPDATE public.credits
  SET balance = balance + p_credits
  WHERE user_id = p_user_id;

  RETURN TRUE;
EXCEPTION WHEN unique_violation THEN
  RETURN FALSE;  -- this external_ref was already processed
END;
$$;
