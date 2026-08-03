"""Bootstrap the administrator for the invite-only app.

Chicken-and-egg: only an admin can invite people, so the first admin has to be
created out of band. This invites `settings.admin_email` (Supabase mails them a
link to set their own password) and marks their row `role = 'admin'`. Safe to
re-run: an existing account is promoted rather than duplicated.

    cd backend && python -m scripts.bootstrap_admin
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import get_settings          # noqa: E402
from app.services.supabase import get_supabase_client  # noqa: E402


def main() -> int:
    settings = get_settings()
    email = (settings.admin_email or "").strip().lower()
    if not email:
        print("ADMIN_EMAIL is not set — nothing to do.")
        return 1

    client = get_supabase_client()

    user_id = None
    try:
        for u in client.auth.admin.list_users():
            if (getattr(u, "email", "") or "").lower() == email:
                user_id = str(u.id)
                break
    except Exception as e:
        print(f"Could not list users: {e}")
        return 1

    created = False
    if user_id is None:
        try:
            invited = client.auth.admin.invite_user_by_email(email)
            user_id = str(invited.user.id)
            created = True
        except Exception as e:
            print(f"Could not invite {email}: {e}")
            return 1

    client.table("users").upsert({
        "id": user_id,
        "email": email,
        "role": "admin",
        "user_type": "internal",   # the administrator is not billed
        "plan_tier": "scale_m2",   # highest tier, so admin testing is never gated
    }).execute()

    print(f"admin ready: {email} ({user_id})")
    print("invitation email sent — they set their own password" if created
          else "existing account promoted to admin")
    return 0


if __name__ == "__main__":
    sys.exit(main())
