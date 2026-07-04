-- ─────────────────────────────────────────────────────────────────────────────
-- Reference image + best-effort character consistency
--
-- character_desc holds a fixed, detailed description of the reference subject
-- (built once via GPT-Vision from projects.reference_image_url). It is injected
-- verbatim into every scene's image prompt, and all scenes share one seed, so
-- SDXL (text-to-image only) keeps the character as consistent as it can. True
-- identity lock (IP-Adapter/InstantID) is a later provider upgrade.
-- ─────────────────────────────────────────────────────────────────────────────

ALTER TABLE public.projects
  ADD COLUMN IF NOT EXISTS character_desc TEXT;
