"""
Seed script: create admin (internal) account + full sample data.

Usage:
    cd backend
    python -m scripts.seed_admin

Will:
  1. Create Supabase auth user (email: yodhimas02@gmail.com)
  2. Set user_type=internal, plan_tier=premium
  3. Seed credits, personas, scripts, voices, video_jobs, campaigns, transactions
"""

import sys
import uuid
from datetime import datetime, timedelta, timezone

# ── bootstrap sys.path so app imports work ────────────────────────────────────
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from supabase import create_client
from app.core.config import get_settings

settings = get_settings()
client   = create_client(settings.supabase_url, settings.supabase_service_role_key)

ADMIN_EMAIL    = "admin@zelugc.app"
ADMIN_PASSWORD = "ZelUGC@Admin2026!"
ADMIN_NAME     = "Admin"

def now_iso(offset_days: int = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=offset_days)).isoformat()

def log(msg: str):
    print(f"  ✓  {msg}")

# ─────────────────────────────────────────────────────────────────────────────
# 1. Auth user
# ─────────────────────────────────────────────────────────────────────────────

def get_or_create_auth_user() -> str:
    # Try to find existing user
    users = client.auth.admin.list_users()
    for u in users:
        if u.email == ADMIN_EMAIL:
            log(f"Auth user already exists: {u.id}")
            return str(u.id)

    # Create new
    resp = client.auth.admin.create_user({
        "email": ADMIN_EMAIL,
        "password": ADMIN_PASSWORD,
        "email_confirm": True,
        "user_metadata": {"name": ADMIN_NAME},
    })
    uid = str(resp.user.id)
    log(f"Created auth user: {uid}")
    return uid

# ─────────────────────────────────────────────────────────────────────────────
# 2. public.users row
# ─────────────────────────────────────────────────────────────────────────────

def upsert_user_row(uid: str):
    client.table("users").upsert({
        "id": uid,
        "user_type": "internal",
        "plan_tier": "premium",
    }).execute()
    log("user_type=internal, plan_tier=premium")

# ─────────────────────────────────────────────────────────────────────────────
# 3. credits
# ─────────────────────────────────────────────────────────────────────────────

def upsert_credits(uid: str):
    client.table("credits").upsert({
        "user_id": uid,
        "balance": 9999,
        "monthly_quota": None,
        "reset_date": None,
    }, on_conflict="user_id").execute()
    log("credits: balance=9999 (unlimited internal)")

# ─────────────────────────────────────────────────────────────────────────────
# 4. Personas
# ─────────────────────────────────────────────────────────────────────────────

PERSONAS = [
    {
        "name": "Aria — Tech Influencer",
        "heygen_avatar_id": "Angela-inblackskirt-20220820",
        "avatar_tier": "avatar_iii",
        "status": "ready",
        "image_url": "https://files.heygen.ai/avatar/v3/Angela-inblackskirt-20220820_preview.jpg",
    },
    {
        "name": "Marcus — Finance Guru",
        "heygen_avatar_id": "Eric_public_3d_pro2",
        "avatar_tier": "avatar_iii",
        "status": "ready",
        "image_url": "https://files.heygen.ai/avatar/v3/Eric_public_3d_pro2_preview.jpg",
    },
    {
        "name": "Zara — Lifestyle Creator",
        "heygen_avatar_id": None,
        "avatar_tier": "avatar_iv",
        "status": "pending",
        "image_url": None,
    },
]

def seed_personas(uid: str) -> list[str]:
    ids = []
    # Delete existing to avoid duplicates on re-run
    existing = client.table("personas").select("id").eq("user_id", uid).execute()
    if existing.data:
        for row in existing.data:
            client.table("personas").delete().eq("id", row["id"]).execute()

    for p in PERSONAS:
        pid = str(uuid.uuid4())
        client.table("personas").insert({
            "id": pid,
            "user_id": uid,
            **p,
            "created_at": now_iso(-30),
        }).execute()
        ids.append(pid)
        log(f"persona: {p['name']}")
    return ids

# ─────────────────────────────────────────────────────────────────────────────
# 5. Scripts
# ─────────────────────────────────────────────────────────────────────────────

SCRIPTS = [
    {
        "title": "5 AI Tools That Changed My Workflow",
        "content": "Let me show you 5 AI tools that completely transformed the way I work...",
        "generation_mode": "ai",
        "is_locked": True,
        "product_name": "AI Productivity Suite",
        "tone": "energetic",
        "audience": "tech professionals",
        "goal": "awareness",
        "style": "listicle",
    },
    {
        "title": "Why You Need to Start Investing NOW",
        "content": "The single biggest financial mistake most people make is waiting too long to invest...",
        "generation_mode": "ai",
        "is_locked": True,
        "product_name": "Investment App",
        "tone": "authoritative",
        "audience": "young adults",
        "goal": "conversion",
        "style": "storytelling",
    },
    {
        "title": "Morning Routine That Gets Results",
        "content": "Here's the exact morning routine I follow every day to stay productive and energized...",
        "generation_mode": "custom",
        "is_locked": True,
        "product_name": None,
        "tone": "friendly",
        "audience": "general",
        "goal": "engagement",
        "style": "vlog",
    },
]

def seed_scripts(uid: str) -> list[str]:
    ids = []
    existing = client.table("scripts").select("id").eq("user_id", uid).execute()
    if existing.data:
        for row in existing.data:
            client.table("scripts").delete().eq("id", row["id"]).execute()

    for s in SCRIPTS:
        sid = str(uuid.uuid4())
        client.table("scripts").insert({
            "id": sid,
            "user_id": uid,
            **s,
            "created_at": now_iso(-20),
        }).execute()
        ids.append(sid)
        log(f"script: {s['title']}")
    return ids

# ─────────────────────────────────────────────────────────────────────────────
# 6. Voice configs
# ─────────────────────────────────────────────────────────────────────────────

VOICES = [
    {"name": "Rachel (ElevenLabs)", "provider": "elevenlabs", "voice_id": "21m00Tcm4TlvDq8ikWAM", "is_custom": False, "validated": True},
    {"name": "Josh (ElevenLabs)",   "provider": "elevenlabs", "voice_id": "TxGEqnHWrfWFTfGW9XjX", "is_custom": False, "validated": True},
    {"name": "Graceful Lady",       "provider": "minimax",    "voice_id": "English_Graceful_Lady",  "is_custom": True,  "validated": True},
]

def seed_voices(uid: str) -> list[str]:
    ids = []
    existing = client.table("voice_configs").select("id").eq("user_id", uid).execute()
    if existing.data:
        for row in existing.data:
            client.table("voice_configs").delete().eq("id", row["id"]).execute()

    for v in VOICES:
        vid = str(uuid.uuid4())
        client.table("voice_configs").insert({
            "id": vid,
            "user_id": uid,
            **v,
            "created_at": now_iso(-15),
        }).execute()
        ids.append(vid)
        log(f"voice: {v['name']}")
    return ids

# ─────────────────────────────────────────────────────────────────────────────
# 7. Video jobs (various statuses for realistic dashboard)
# ─────────────────────────────────────────────────────────────────────────────

def seed_video_jobs(uid: str, persona_ids: list[str], script_ids: list[str], voice_ids: list[str]) -> list[str]:
    ids = []
    existing = client.table("video_jobs").select("id").eq("user_id", uid).execute()
    if existing.data:
        for row in existing.data:
            client.table("video_jobs").delete().eq("id", row["id"]).execute()

    jobs = [
        {
            "status": "completed",
            "persona_id": persona_ids[0],
            "script_id": script_ids[0],
            "voice_config_id": voice_ids[0],
            "format": "9:16",
            "duration_seconds": 60,
            "credits_used": 0,
            "final_video_url": "https://example.com/video_completed_1.mp4",
            "processing_time_ms": 42000,
            "created_at": now_iso(-7),
            "completed_at": now_iso(-7),
        },
        {
            "status": "completed",
            "persona_id": persona_ids[1],
            "script_id": script_ids[1],
            "voice_config_id": voice_ids[1],
            "format": "9:16",
            "duration_seconds": 45,
            "credits_used": 0,
            "final_video_url": "https://example.com/video_completed_2.mp4",
            "processing_time_ms": 38000,
            "created_at": now_iso(-5),
            "completed_at": now_iso(-5),
        },
        {
            "status": "completed",
            "persona_id": persona_ids[0],
            "script_id": script_ids[2],
            "voice_config_id": voice_ids[2],
            "format": "16:9",
            "duration_seconds": 30,
            "credits_used": 0,
            "final_video_url": "https://example.com/video_completed_3.mp4",
            "processing_time_ms": 29000,
            "created_at": now_iso(-3),
            "completed_at": now_iso(-3),
        },
        {
            "status": "rendering",
            "persona_id": persona_ids[1],
            "script_id": script_ids[0],
            "voice_config_id": voice_ids[0],
            "format": "9:16",
            "duration_seconds": 60,
            "credits_used": 0,
            "final_video_url": None,
            "created_at": now_iso(-1),
            "completed_at": None,
        },
        {
            "status": "failed",
            "persona_id": persona_ids[0],
            "script_id": script_ids[1],
            "voice_config_id": voice_ids[1],
            "format": "1:1",
            "duration_seconds": 30,
            "credits_used": 0,
            "error_message": "HeyGen rendering timeout after 15 minutes",
            "final_video_url": None,
            "created_at": now_iso(-10),
            "completed_at": None,
        },
    ]

    for j in jobs:
        jid = str(uuid.uuid4())
        client.table("video_jobs").insert({
            "id": jid,
            "user_id": uid,
            "request_id": str(uuid.uuid4()),
            "motion_intensity": "moderate",
            "subtitle_enabled": True,
            "subtitle_font": "bold",
            "subtitle_size": 32,
            "subtitle_color": "#FFFFFF",
            "subtitle_position": "bottom",
            **j,
        }).execute()
        ids.append(jid)
        log(f"video_job: {j['status']} ({j['format']}, {j['duration_seconds']}s)")
    return ids

# ─────────────────────────────────────────────────────────────────────────────
# 8. Campaigns
# ─────────────────────────────────────────────────────────────────────────────

def seed_campaigns(uid: str, persona_ids: list[str], script_ids: list[str], voice_ids: list[str]):
    existing = client.table("campaigns").select("id").eq("user_id", uid).execute()
    if existing.data:
        for row in existing.data:
            client.table("campaigns").delete().eq("id", row["id"]).execute()

    campaigns = [
        {
            "name": "Daily Tech Tips",
            "persona_id": persona_ids[0],
            "script_id": script_ids[0],
            "voice_config_id": voice_ids[0],
            "schedule_type": "daily",
            "schedule_cron": "0 9 * * *",
            "next_run_at": now_iso(1),
            "status": "active",
        },
        {
            "name": "Weekly Finance Update",
            "persona_id": persona_ids[1],
            "script_id": script_ids[1],
            "voice_config_id": voice_ids[1],
            "schedule_type": "weekly",
            "schedule_cron": "0 10 * * 1",
            "next_run_at": now_iso(3),
            "status": "active",
        },
        {
            "name": "Lifestyle Content (Paused)",
            "persona_id": persona_ids[0],
            "script_id": script_ids[2],
            "voice_config_id": voice_ids[2],
            "schedule_type": "weekly",
            "schedule_cron": "0 14 * * 5",
            "next_run_at": None,
            "status": "paused",
        },
    ]

    for c in campaigns:
        client.table("campaigns").insert({
            "id": str(uuid.uuid4()),
            "user_id": uid,
            **c,
            "created_at": now_iso(-14),
        }).execute()
        log(f"campaign: {c['name']} ({c['status']})")

# ─────────────────────────────────────────────────────────────────────────────
# 9. Credit transactions
# ─────────────────────────────────────────────────────────────────────────────

def seed_transactions(uid: str, job_ids: list[str]):
    existing = client.table("credit_transactions").select("id").eq("user_id", uid).execute()
    if existing.data:
        for row in existing.data:
            client.table("credit_transactions").delete().eq("id", row["id"]).execute()

    txs = [
        {"amount": 100,  "type": "grant",     "video_job_id": None,       "created_at": now_iso(-30)},
        {"amount": -2,   "type": "deduction",  "video_job_id": job_ids[4], "created_at": now_iso(-10)},
        {"amount": -2,   "type": "deduction",  "video_job_id": job_ids[0], "created_at": now_iso(-7)},
        {"amount": 2,    "type": "refund",     "video_job_id": job_ids[4], "created_at": now_iso(-6)},
        {"amount": -2,   "type": "deduction",  "video_job_id": job_ids[1], "created_at": now_iso(-5)},
        {"amount": -1,   "type": "deduction",  "video_job_id": job_ids[2], "created_at": now_iso(-3)},
    ]

    for tx in txs:
        client.table("credit_transactions").insert({
            "id": str(uuid.uuid4()),
            "user_id": uid,
            **tx,
        }).execute()
        amt: int = tx['amount']  # type: ignore[assignment]
        log(f"transaction: {'+' if amt > 0 else ''}{amt} ({tx['type']})")

# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    print("\n🌱  Seeding admin account...\n")

    uid = get_or_create_auth_user()
    upsert_user_row(uid)
    upsert_credits(uid)

    persona_ids = seed_personas(uid)
    script_ids  = seed_scripts(uid)
    voice_ids   = seed_voices(uid)
    job_ids     = seed_video_jobs(uid, persona_ids, script_ids, voice_ids)
    seed_campaigns(uid, persona_ids, script_ids, voice_ids)
    seed_transactions(uid, job_ids)

    print(f"\n✅  Done! Login dengan:\n")
    print(f"   Email   : {ADMIN_EMAIL}")
    print(f"   Password: {ADMIN_PASSWORD}")
    print(f"   User ID : {uid}\n")

if __name__ == "__main__":
    main()
