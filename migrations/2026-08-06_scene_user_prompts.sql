-- ─────────────────────────────────────────────────────────────────────────────
-- Manual image prompt per scene (Faith, 2026-08-06)
--
-- AI-written image prompts were not matching the narration closely enough, which
-- was hurting video quality. The user now writes the image prompt for every
-- scene themselves, and the renderer uses that text EXACTLY as given.
--
--   scenes.image_prompt  the AI's suggestion from the breakdown (kept for
--                        reference and as the fallback for older projects)
--   scenes.user_prompt   what the user actually wrote — authoritative when set
--
-- A render is blocked until every scene has a user_prompt, so the column stays
-- nullable: it is empty for the whole time the user is filling the form in.
-- ─────────────────────────────────────────────────────────────────────────────

ALTER TABLE public.scenes
  ADD COLUMN IF NOT EXISTS user_prompt TEXT;


-- ── higher-resolution output for Pro and Scale (Faith, 2026-08-06) ───────────
-- Short edge of the render canvas, stamped on the project when the render is
-- kicked off so every scene agrees on it even if the plan changes mid-render.
-- 1080 is standard; Pro and Scale render at 1440.
ALTER TABLE public.projects
  ADD COLUMN IF NOT EXISTS render_height INTEGER NOT NULL DEFAULT 1080;

-- Projects that were already rendered keep working: their existing AI prompt is
-- copied in, so re-rendering one does not suddenly demand manual input.
UPDATE public.scenes
SET user_prompt = image_prompt
WHERE user_prompt IS NULL
  AND image_prompt IS NOT NULL
  AND project_id IN (SELECT id FROM public.projects WHERE status = 'completed');
