-- ─────────────────────────────────────────────────────────────────────────────
-- Store target_audience on saved scripts
--
-- save_script() was writing to a column named "audience", which never existed
-- on public.scripts (every other layer of the stack uses "target_audience").
-- The insert silently dropped the value, so "reuse a saved script" could never
-- restore audience. Add the correctly-named column.
-- ─────────────────────────────────────────────────────────────────────────────

ALTER TABLE public.scripts
  ADD COLUMN IF NOT EXISTS target_audience TEXT;
