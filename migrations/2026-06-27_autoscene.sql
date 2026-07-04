-- ================================================================
-- AutoScene — scene-based video pipeline (Mode 1 & Mode 2)
-- Run in: Supabase Dashboard → SQL Editor
--
-- Introduces the project-based model (UI: Script → Scenes → Configure →
-- Generate). A `project` is the render job container; `scenes` are its child
-- rows. The legacy avatar tables (personas/video_jobs) are LEFT IN PLACE so
-- nothing breaks during the migration; they will be retired separately.
-- ================================================================

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ── projects ──────────────────────────────────────────────────────────────────
-- One row per video the user is building. Carries the pipeline state machine and
-- all the configuration captured on the Configure step.
CREATE TABLE IF NOT EXISTS public.projects (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id             UUID NOT NULL REFERENCES public.users ON DELETE CASCADE,
  name                TEXT NOT NULL DEFAULT 'Untitled Project',

  -- inputs
  script_id           UUID REFERENCES public.scripts ON DELETE SET NULL,
  voice_config_id     UUID REFERENCES public.voice_configs ON DELETE SET NULL,
  reference_image_url TEXT,                              -- optional character reference

  -- idempotency (client-generated UUID, like video_jobs.request_id)
  request_id          TEXT UNIQUE,

  -- configuration (Configure step)
  render_mode         TEXT NOT NULL DEFAULT 'mode_1',    -- mode_1 (1 img/scene), mode_2 (3 img/scene)
  format              TEXT NOT NULL DEFAULT '9:16',      -- 16:9, 9:16
  niche               TEXT,
  style               TEXT,                              -- visual style (Cinematic, Cartoon, ...)
  duration_seconds    INTEGER NOT NULL DEFAULT 60,
  scene_duration_seconds INTEGER NOT NULL DEFAULT 10,

  -- subtitles (mirror video_jobs columns)
  subtitle_enabled    BOOLEAN NOT NULL DEFAULT TRUE,
  subtitle_font       TEXT    DEFAULT 'bold',
  subtitle_size       INTEGER DEFAULT 24,
  subtitle_color      TEXT    DEFAULT '#FFFFFF',
  subtitle_position   TEXT    DEFAULT 'bottom',

  -- pipeline state
  status              TEXT NOT NULL DEFAULT 'draft',
  -- draft, pending, scripting, scene_breakdown, scenes_ready,
  -- generating_images, rendering_scenes, voiceover, assembling, completed,
  -- failed_at_<stage>, cancelled
  credits_used        INTEGER NOT NULL DEFAULT 0,

  -- outputs
  voiceover_url       TEXT,
  final_video_url     TEXT,
  thumbnail_urls      JSONB,
  transcription       JSONB,

  -- bookkeeping
  error_message       TEXT,
  processing_time_ms  INTEGER,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  completed_at        TIMESTAMPTZ
);

-- ── scenes ────────────────────────────────────────────────────────────────────
-- One row per ~10s scene. user_id is denormalised so RLS is a simple equality
-- check (no join) and reads stay fast.
CREATE TABLE IF NOT EXISTS public.scenes (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id        UUID NOT NULL REFERENCES public.projects ON DELETE CASCADE,
  user_id           UUID NOT NULL REFERENCES public.users ON DELETE CASCADE,
  idx               INTEGER NOT NULL,                    -- 0-based order within the project

  -- scene breakdown engine output
  scene_text        TEXT,                                -- narration text for this scene
  emotion           TEXT,
  action            TEXT,
  environment       TEXT,
  image_prompt      TEXT,                                -- base image prompt
  -- Mode 2 progressive prompts (A→B→C). Empty/absent for Mode 1.
  image_prompts     JSONB,                               -- ["A", "B", "C"]

  -- generation
  seed              BIGINT,                              -- locks identity across frames
  motion_type       TEXT,                                -- zoom_in, zoom_out, pan_left, pan_right, zoom_pan
  image_urls        JSONB,                               -- [url] for mode_1, [A,B,C] for mode_2
  clip_url          TEXT,                                -- rendered scene clip
  duration_seconds  NUMERIC NOT NULL DEFAULT 10,

  status            TEXT NOT NULL DEFAULT 'pending',     -- pending, prompted, image_ready, rendered, failed
  error_message     TEXT,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS scenes_project_idx_unique ON public.scenes(project_id, idx);

-- ── reuse job_events / pipeline_metrics for the project pipeline ───────────────
-- Add a nullable project_id and relax video_job_id so the SAME events/metrics
-- infra (and its Realtime feed) serves both the legacy and AutoScene pipelines.
ALTER TABLE public.job_events       ADD COLUMN IF NOT EXISTS project_id UUID REFERENCES public.projects ON DELETE CASCADE;
ALTER TABLE public.job_events       ALTER COLUMN video_job_id DROP NOT NULL;
ALTER TABLE public.pipeline_metrics ADD COLUMN IF NOT EXISTS project_id UUID REFERENCES public.projects ON DELETE CASCADE;
ALTER TABLE public.pipeline_metrics ALTER COLUMN video_job_id DROP NOT NULL;

-- ── indexes ───────────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_projects_user      ON public.projects(user_id);
CREATE INDEX IF NOT EXISTS idx_projects_status    ON public.projects(status);
CREATE INDEX IF NOT EXISTS idx_projects_request   ON public.projects(request_id);
CREATE INDEX IF NOT EXISTS idx_scenes_project     ON public.scenes(project_id);
CREATE INDEX IF NOT EXISTS idx_scenes_user        ON public.scenes(user_id);
CREATE INDEX IF NOT EXISTS idx_job_events_project ON public.job_events(project_id);

-- ── row level security ────────────────────────────────────────────────────────
ALTER TABLE public.projects ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.scenes   ENABLE ROW LEVEL SECURITY;

CREATE POLICY "projects_own_data" ON public.projects FOR ALL USING (user_id = auth.uid());
CREATE POLICY "scenes_own_data"   ON public.scenes   FOR ALL USING (user_id = auth.uid());

-- job_events already has an own-data policy keyed on video_job_id; extend it so
-- project-scoped events are visible to the owner too.
DROP POLICY IF EXISTS "job_events_own_data" ON public.job_events;
CREATE POLICY "job_events_own_data" ON public.job_events FOR ALL
  USING (
    (video_job_id IS NOT NULL AND video_job_id IN (SELECT id FROM public.video_jobs WHERE user_id = auth.uid()))
    OR
    (project_id   IS NOT NULL AND project_id   IN (SELECT id FROM public.projects   WHERE user_id = auth.uid()))
  );

DROP POLICY IF EXISTS "pipeline_metrics_own_data" ON public.pipeline_metrics;
CREATE POLICY "pipeline_metrics_own_data" ON public.pipeline_metrics FOR ALL
  USING (
    (video_job_id IS NOT NULL AND video_job_id IN (SELECT id FROM public.video_jobs WHERE user_id = auth.uid()))
    OR
    (project_id   IS NOT NULL AND project_id   IN (SELECT id FROM public.projects   WHERE user_id = auth.uid()))
  );

-- ── atomic: start a project render (deduct credits + flip to pending) ──────────
-- A project is created as a 'draft' first (no charge) and edited across the
-- Script/Scenes/Configure steps, so the render kickoff DEDUCTS against the
-- existing row rather than inserting a new one. Locks the credits row, verifies
-- balance, deducts, logs the transaction, and stamps the project — all atomic.
CREATE OR REPLACE FUNCTION public.start_project_render_atomic(
  p_user_id     UUID,
  p_project_id  UUID,
  p_credits     INTEGER,
  p_request_id  TEXT
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
  v_balance INTEGER;
BEGIN
  SELECT balance INTO v_balance
  FROM public.credits
  WHERE user_id = p_user_id
  FOR UPDATE;

  IF v_balance < p_credits THEN
    RAISE EXCEPTION 'Insufficient credits: need %, have %', p_credits, v_balance;
  END IF;

  UPDATE public.credits
  SET balance = balance - p_credits
  WHERE user_id = p_user_id;

  INSERT INTO public.credit_transactions (user_id, amount, type, video_job_id)
  VALUES (p_user_id, -p_credits, 'deduction', p_project_id);

  UPDATE public.projects
  SET status = 'pending',
      credits_used = p_credits,
      request_id = p_request_id,
      error_message = NULL
  WHERE id = p_project_id AND user_id = p_user_id;

  RETURN jsonb_build_object('project_id', p_project_id, 'credits', p_credits);
END;
$$;

-- refund_credits(p_user_id, p_job_id, p_credits) already exists and is generic
-- (credit_transactions.video_job_id has no FK), so it works for project ids too.

-- ── realtime (frontend live updates) ──────────────────────────────────────────
ALTER PUBLICATION supabase_realtime ADD TABLE public.projects;
ALTER PUBLICATION supabase_realtime ADD TABLE public.scenes;
