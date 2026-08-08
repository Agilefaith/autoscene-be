-- ─────────────────────────────────────────────────────────────────────────────
-- Fix: add_topup_credits() keys its idempotency on credit_transactions.external_ref,
-- but that column was only ever created by 2026-06-18_stripe_to_lemonsqueezy.sql,
-- which was never run against this database. Without it every Pay-As-You-Go
-- webhook raised "column external_ref does not exist" and no credits were granted.
--
-- Run this AFTER 2026-08-05_credits_minutes.sql.
-- ─────────────────────────────────────────────────────────────────────────────

ALTER TABLE public.credit_transactions
  ADD COLUMN IF NOT EXISTS external_ref TEXT;

-- The unique index is what makes add_topup_credits idempotent: a webhook Paystack
-- re-delivers hits unique_violation and grants nothing the second time.
CREATE UNIQUE INDEX IF NOT EXISTS credit_tx_external_ref_unique
  ON public.credit_transactions(external_ref) WHERE external_ref IS NOT NULL;
