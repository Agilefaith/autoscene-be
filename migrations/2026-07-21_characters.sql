-- ─────────────────────────────────────────────────────────────────────────────
-- Multi-character identity lock (Faith feedback 2026-07-17)
--
-- Replaces the single reference_image_url + character_desc pair with a named
-- cast: the user uploads one reference image per character and names it, so the
-- scene engine can say "Leah (long wavy brown hair, cream dress)" by name in
-- every scene prompt, and the image engine receives every character's reference.
--
-- Shape: [{"name": "Leah", "image_url": "https://…", "description": "…"}]
--   name        — user-provided, used verbatim in scene/image prompts
--   image_url   — Backblaze URL of the uploaded reference
--   description — locked physical description built once by GPT-Vision
--                 (app/services/character_sheet.py) and reused across ALL scenes
--
-- reference_image_url is kept for backwards compatibility with existing projects
-- (treated as an unnamed single character when `characters` is empty).
-- ─────────────────────────────────────────────────────────────────────────────

ALTER TABLE public.projects
  ADD COLUMN IF NOT EXISTS characters JSONB NOT NULL DEFAULT '[]'::jsonb;
