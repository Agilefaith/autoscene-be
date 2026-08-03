-- ─────────────────────────────────────────────────────────────────────────────
-- Invite-only access + roles (Faith, 2026-08-03)
--
-- The app becomes private: nobody can sign up on their own. The admin invites a
-- person by email and picks the plan they land on; Supabase sends the invitation
-- and the invitee sets their own password. The public landing page stays visible
-- to everyone — only the app behind it is locked.
--
--   users.role  'admin' = can invite and manage users. 'user' = everyone else.
--   invites     one row per invitation, so the admin can see who was invited,
--               on which plan, and whether they have accepted yet.
-- ─────────────────────────────────────────────────────────────────────────────

ALTER TABLE public.users
  ADD COLUMN IF NOT EXISTS role  TEXT NOT NULL DEFAULT 'user',
  ADD COLUMN IF NOT EXISTS email TEXT;

CREATE TABLE IF NOT EXISTS public.invites (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  email       TEXT NOT NULL,
  plan_id     TEXT NOT NULL DEFAULT 'free',   -- plan the invitee starts on
  status      TEXT NOT NULL DEFAULT 'pending',-- pending | accepted | revoked
  invited_by  UUID REFERENCES public.users ON DELETE SET NULL,
  user_id     UUID REFERENCES public.users ON DELETE SET NULL,  -- filled on accept
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  accepted_at TIMESTAMPTZ
);

-- One outstanding invitation per address; re-inviting a revoked/accepted address
-- is still allowed.
CREATE UNIQUE INDEX IF NOT EXISTS invites_pending_email_idx
  ON public.invites (lower(email)) WHERE status = 'pending';

CREATE INDEX IF NOT EXISTS invites_status_idx ON public.invites (status, created_at DESC);

-- Faith is the only admin, and the only account that can invite others.
UPDATE public.users u
   SET role = 'admin'
  FROM auth.users a
 WHERE a.id = u.id
   AND lower(a.email) = 'faithfuliselen@gmail.com';

-- Backfill the email column so the admin screen can list people by address.
UPDATE public.users u
   SET email = a.email
  FROM auth.users a
 WHERE a.id = u.id AND u.email IS NULL;
