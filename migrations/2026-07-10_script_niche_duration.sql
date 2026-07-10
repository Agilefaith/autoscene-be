-- ─────────────────────────────────────────────────────────────────────────────
-- Store niche + target duration on saved scripts
--
-- The "reuse a saved script" flow in the create wizard only restored
-- title/product/style/goal/tone because scripts never persisted niche or
-- duration in the first place — those columns existed on projects but not
-- on scripts. Add them here so reuse can restore the full config.
-- ─────────────────────────────────────────────────────────────────────────────

ALTER TABLE public.scripts
  ADD COLUMN IF NOT EXISTS niche TEXT,
  ADD COLUMN IF NOT EXISTS duration_seconds INTEGER;
