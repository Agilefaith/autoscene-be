-- ─────────────────────────────────────────────────────────────────────────────
-- 2026-07-01  Drop the legacy avatar/HeyGen layer (Zel UGC → AutoScene)
-- ─────────────────────────────────────────────────────────────────────────────
-- The product pivoted to the faceless, scene-based pipeline (projects + scenes).
-- The avatar pipeline (video_jobs, personas) is removed. `campaigns` is KEPT (its
-- code/route is left dormant) but loses its persona_id FK so personas can drop.
--
-- DESTRUCTIVE: drops video_jobs + personas and their rows. Assumes no production
-- data (pre-launch). Run on a Supabase branch / dev DB first. Order matters: RLS
-- policies and FKs that reference the legacy tables must be removed before the
-- tables themselves.
-- ─────────────────────────────────────────────────────────────────────────────

BEGIN;

-- 1) Re-key job_events / pipeline_metrics RLS to project-only (drop the video_jobs
--    sub-select). These policies are recreated to no longer reference video_jobs.
DROP POLICY IF EXISTS "job_events_own_data" ON public.job_events;
CREATE POLICY "job_events_own_data" ON public.job_events FOR ALL
  USING (
    project_id IS NOT NULL
    AND project_id IN (SELECT id FROM public.projects WHERE user_id = auth.uid())
  );

DROP POLICY IF EXISTS "pipeline_metrics_own_data" ON public.pipeline_metrics;
CREATE POLICY "pipeline_metrics_own_data" ON public.pipeline_metrics FOR ALL
  USING (
    project_id IS NOT NULL
    AND project_id IN (SELECT id FROM public.projects WHERE user_id = auth.uid())
  );

-- 2) Drop the now-unused video_job_id columns (project_id remains the only key).
--    credit_transactions.video_job_id is intentionally KEPT — it has no FK and is
--    reused to reference project ids (see start_project_render_atomic).
ALTER TABLE public.job_events       DROP COLUMN IF EXISTS video_job_id;
ALTER TABLE public.pipeline_metrics DROP COLUMN IF EXISTS video_job_id;

-- 3) Drop the legacy atomic job-creation function (replaced by start_project_render_atomic).
DROP FUNCTION IF EXISTS public.create_video_job_atomic(UUID, JSONB, INTEGER);

-- 4) Sever campaigns → personas so personas can be dropped. campaigns stays
--    (dormant); losing persona_id is harmless until campaigns is ported to projects.
ALTER TABLE public.campaigns DROP COLUMN IF EXISTS persona_id;

-- 5) Drop the legacy tables. CASCADE removes their RLS policies, indexes, FKs, and
--    Realtime publication membership.
DROP TABLE IF EXISTS public.video_jobs CASCADE;
DROP TABLE IF EXISTS public.personas  CASCADE;

COMMIT;
