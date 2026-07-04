-- ================================================================
-- Vidora — Supabase Database Migration
-- Run this in: Supabase Dashboard → SQL Editor
-- ================================================================

-- ── Enable UUID extension ─────────────────────────────────────────────────────
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ── users (extends auth.users) ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.users (
  id           UUID PRIMARY KEY REFERENCES auth.users ON DELETE CASCADE,
  user_type    TEXT NOT NULL DEFAULT 'trial',    -- trial, standard, internal
  plan_tier    TEXT NOT NULL DEFAULT 'free',     -- free, pro, premium
  lemonsqueezy_customer_id TEXT,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ── credits ───────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.credits (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id        UUID NOT NULL REFERENCES public.users ON DELETE CASCADE,
  balance        INTEGER NOT NULL DEFAULT 1,
  monthly_quota  INTEGER,
  reset_date     TIMESTAMPTZ
);
CREATE UNIQUE INDEX IF NOT EXISTS credits_user_unique ON public.credits(user_id);

-- ── credit_transactions ───────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.credit_transactions (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id        UUID NOT NULL REFERENCES public.users ON DELETE CASCADE,
  amount         INTEGER NOT NULL,               -- positive = add, negative = deduct
  type           TEXT NOT NULL,                  -- purchase, deduction, refund, grant
  video_job_id   UUID,
  external_ref   TEXT,                           -- idempotency key (e.g. ls_order_<id>)
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS credit_tx_external_ref_unique
  ON public.credit_transactions(external_ref) WHERE external_ref IS NOT NULL;

-- ── personas ──────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.personas (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id           UUID NOT NULL REFERENCES public.users ON DELETE CASCADE,
  name              TEXT NOT NULL,
  image_url         TEXT,
  heygen_avatar_id  TEXT,
  avatar_tier       TEXT NOT NULL DEFAULT 'avatar_iii',  -- avatar_iii, avatar_iv
  status            TEXT NOT NULL DEFAULT 'pending',     -- pending, ready, failed
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ── scripts ───────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.scripts (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id          UUID NOT NULL REFERENCES public.users ON DELETE CASCADE,
  title            TEXT NOT NULL,
  content          TEXT NOT NULL,
  generation_mode  TEXT NOT NULL DEFAULT 'ai',           -- ai, custom
  is_locked        BOOLEAN NOT NULL DEFAULT false,       -- true = custom (immutable)
  product_name     TEXT,
  tone             TEXT,
  audience         TEXT,
  goal             TEXT,
  style            TEXT,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ── voice_configs ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.voice_configs (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id     UUID NOT NULL REFERENCES public.users ON DELETE CASCADE,
  name        TEXT NOT NULL,
  provider    TEXT NOT NULL DEFAULT 'elevenlabs',  -- elevenlabs, minimax
  voice_id    TEXT NOT NULL,
  is_custom   BOOLEAN NOT NULL DEFAULT false,
  validated   BOOLEAN NOT NULL DEFAULT false,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ── video_jobs ────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.video_jobs (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id               UUID NOT NULL REFERENCES public.users ON DELETE CASCADE,
  -- SET NULL so a persona can be deleted while its historical jobs/videos remain.
  persona_id            UUID REFERENCES public.personas ON DELETE SET NULL,
  script_id             UUID REFERENCES public.scripts,
  voice_config_id       UUID REFERENCES public.voice_configs,
  request_id            TEXT UNIQUE,              -- idempotency key from frontend
  status                TEXT NOT NULL DEFAULT 'pending',
  format                TEXT,                     -- 16:9, 9:16, 1:1, 4:5
  resolution            TEXT,
  duration_seconds      INTEGER,
  motion_intensity      TEXT,                     -- subtle, moderate, expressive, engaging
  subtitle_enabled      BOOLEAN NOT NULL DEFAULT TRUE,
  subtitle_font         TEXT,
  subtitle_size         INTEGER,
  subtitle_color        TEXT,
  subtitle_position     TEXT,
  credits_used          INTEGER NOT NULL DEFAULT 0,
  -- Stage outputs (preserved for retry)
  generated_script      TEXT,
  motion_prompts        JSONB,
  edit_decision_list    JSONB,
  heygen_job_id         TEXT,
  rendered_video_url    TEXT,
  transcription         JSONB,
  final_video_url       TEXT,
  -- Timing + errors
  error_message         TEXT,
  processing_time_ms    INTEGER,
  created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  completed_at          TIMESTAMPTZ
);

-- ── job_events ────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.job_events (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  video_job_id   UUID NOT NULL REFERENCES public.video_jobs ON DELETE CASCADE,
  stage          TEXT NOT NULL,
  status         TEXT NOT NULL,
  duration_ms    INTEGER,
  metadata       JSONB,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ── campaigns ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.campaigns (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id          UUID NOT NULL REFERENCES public.users ON DELETE CASCADE,
  name             TEXT NOT NULL,
  persona_id       UUID REFERENCES public.personas,
  script_id        UUID REFERENCES public.scripts,
  voice_config_id  UUID REFERENCES public.voice_configs,
  schedule_type    TEXT NOT NULL DEFAULT 'daily',   -- daily, weekly, custom
  schedule_cron    TEXT NOT NULL,
  next_run_at      TIMESTAMPTZ,
  status           TEXT NOT NULL DEFAULT 'active',  -- active, paused
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ── subscriptions ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.subscriptions (
  id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id                 UUID NOT NULL REFERENCES public.users ON DELETE CASCADE,
  lemonsqueezy_subscription_id  TEXT UNIQUE,
  plan_tier               TEXT NOT NULL,
  status                  TEXT NOT NULL,
  current_period_start    TIMESTAMPTZ,
  current_period_end      TIMESTAMPTZ,
  created_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ── pipeline_metrics ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.pipeline_metrics (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  video_job_id      UUID NOT NULL REFERENCES public.video_jobs ON DELETE CASCADE,
  stage             TEXT NOT NULL,
  duration_ms       INTEGER,
  api_cost_estimate DECIMAL,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ================================================================
-- INDEXES
-- ================================================================
CREATE INDEX IF NOT EXISTS idx_personas_user       ON public.personas(user_id);
CREATE INDEX IF NOT EXISTS idx_scripts_user        ON public.scripts(user_id);
CREATE INDEX IF NOT EXISTS idx_voice_configs_user  ON public.voice_configs(user_id);
CREATE INDEX IF NOT EXISTS idx_video_jobs_user     ON public.video_jobs(user_id);
CREATE INDEX IF NOT EXISTS idx_video_jobs_status   ON public.video_jobs(status);
CREATE INDEX IF NOT EXISTS idx_video_jobs_request  ON public.video_jobs(request_id);
CREATE INDEX IF NOT EXISTS idx_campaigns_user      ON public.campaigns(user_id);
CREATE INDEX IF NOT EXISTS idx_campaigns_next_run  ON public.campaigns(next_run_at);
CREATE INDEX IF NOT EXISTS idx_job_events_job      ON public.job_events(video_job_id);
CREATE INDEX IF NOT EXISTS idx_credit_tx_user      ON public.credit_transactions(user_id);

-- ================================================================
-- ROW LEVEL SECURITY
-- ================================================================
ALTER TABLE public.users              ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.credits            ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.credit_transactions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.personas           ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.scripts            ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.voice_configs      ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.video_jobs         ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.job_events         ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.campaigns          ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.subscriptions      ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.pipeline_metrics   ENABLE ROW LEVEL SECURITY;

-- Users can only see and modify their own data
CREATE POLICY "users_own_data"         ON public.users              FOR ALL USING (id = auth.uid());
CREATE POLICY "credits_own_data"       ON public.credits            FOR ALL USING (user_id = auth.uid());
CREATE POLICY "credit_tx_own_data"     ON public.credit_transactions FOR ALL USING (user_id = auth.uid());
CREATE POLICY "personas_own_data"      ON public.personas           FOR ALL USING (user_id = auth.uid());
CREATE POLICY "scripts_own_data"       ON public.scripts            FOR ALL USING (user_id = auth.uid());
CREATE POLICY "voice_configs_own_data" ON public.voice_configs      FOR ALL USING (user_id = auth.uid());
CREATE POLICY "video_jobs_own_data"    ON public.video_jobs         FOR ALL USING (user_id = auth.uid());
CREATE POLICY "campaigns_own_data"     ON public.campaigns          FOR ALL USING (user_id = auth.uid());
CREATE POLICY "subscriptions_own_data" ON public.subscriptions      FOR ALL USING (user_id = auth.uid());

-- job_events: readable via parent video_jobs
CREATE POLICY "job_events_own_data" ON public.job_events FOR ALL
  USING (
    video_job_id IN (
      SELECT id FROM public.video_jobs WHERE user_id = auth.uid()
    )
  );

-- pipeline_metrics: readable via parent video_jobs
CREATE POLICY "pipeline_metrics_own_data" ON public.pipeline_metrics FOR ALL
  USING (
    video_job_id IN (
      SELECT id FROM public.video_jobs WHERE user_id = auth.uid()
    )
  );

-- ================================================================
-- STORED PROCEDURES (for atomic credit operations)
-- ================================================================

-- Atomically deduct credits and create a video job
CREATE OR REPLACE FUNCTION public.create_video_job_atomic(
  p_user_id     UUID,
  p_job_payload JSONB,
  p_credits     INTEGER
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
  v_balance INTEGER;
  v_job     JSONB;
BEGIN
  -- Lock the credits row
  SELECT balance INTO v_balance
  FROM public.credits
  WHERE user_id = p_user_id
  FOR UPDATE;

  IF v_balance < p_credits THEN
    RAISE EXCEPTION 'Insufficient credits: need %, have %', p_credits, v_balance;
  END IF;

  -- Deduct credits
  UPDATE public.credits
  SET balance = balance - p_credits
  WHERE user_id = p_user_id;

  -- Log transaction
  INSERT INTO public.credit_transactions (user_id, amount, type, video_job_id)
  VALUES (p_user_id, -p_credits, 'deduction', (p_job_payload->>'id')::UUID);

  -- Insert job row
  INSERT INTO public.video_jobs
  SELECT * FROM jsonb_populate_record(NULL::public.video_jobs, p_job_payload);

  RETURN p_job_payload;
END;
$$;

-- Refund credits on job failure
CREATE OR REPLACE FUNCTION public.refund_credits(
  p_user_id UUID,
  p_job_id  UUID,
  p_credits INTEGER
)
RETURNS VOID
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
BEGIN
  UPDATE public.credits
  SET balance = balance + p_credits
  WHERE user_id = p_user_id;

  INSERT INTO public.credit_transactions (user_id, amount, type, video_job_id)
  VALUES (p_user_id, p_credits, 'refund', p_job_id);
END;
$$;

-- Grant credits exactly once for a given external reference (e.g. a Lemon
-- Squeezy order). Returns TRUE if granted, FALSE if the ref was already
-- processed — making credit-pack webhook deliveries idempotent.
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

-- ================================================================
-- TRIGGER: auto-create user row + trial credit on any signup
-- Covers email/password AND Google OAuth (any Supabase provider)
-- ================================================================
CREATE OR REPLACE FUNCTION public.handle_new_auth_user()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER SET search_path = public
AS $$
BEGIN
  -- Skip if user row already exists (idempotent)
  INSERT INTO public.users (id, user_type, plan_tier)
  VALUES (new.id, 'trial', 'free')
  ON CONFLICT (id) DO NOTHING;

  INSERT INTO public.credits (user_id, balance)
  VALUES (new.id, 1)
  ON CONFLICT (user_id) DO NOTHING;

  RETURN new;
END;
$$;

CREATE OR REPLACE TRIGGER on_auth_user_created
  AFTER INSERT ON auth.users
  FOR EACH ROW EXECUTE FUNCTION public.handle_new_auth_user();

-- ================================================================
-- REALTIME (enable for frontend live updates)
-- ================================================================
ALTER PUBLICATION supabase_realtime ADD TABLE public.video_jobs;
ALTER PUBLICATION supabase_realtime ADD TABLE public.job_events;
