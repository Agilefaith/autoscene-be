-- ─────────────────────────────────────────────────────────────────────────────
-- Per-user voice API keys (Faith decision 2026-07-24)
--
-- ElevenLabs/Minimax voice IDs are scoped to the account that owns them, so a
-- private cloned voice can never be synthesized with the platform's key. Users
-- can now attach their OWN provider API key to a custom voice config; the
-- pipeline then synthesizes that voice with their key instead of the platform's.
--
-- The key is encrypted at rest with Fernet (app/services/crypto.py) and is
-- never returned by the API (schemas/voice.py VoiceResponse has no key field).
-- ─────────────────────────────────────────────────────────────────────────────

ALTER TABLE public.voice_configs
  ADD COLUMN IF NOT EXISTS api_key_encrypted TEXT;
