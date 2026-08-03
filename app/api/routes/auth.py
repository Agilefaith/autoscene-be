import re
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, EmailStr, field_validator
from app.services.supabase import get_supabase_client
from app.core.config import get_settings

router = APIRouter(prefix="/auth", tags=["auth"])
settings = get_settings()


class SignUpRequest(BaseModel):
    email: EmailStr
    password: str
    name: str

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        errors = []
        if len(v) < 8:
            errors.append("minimal 8 karakter")
        if not re.search(r"[A-Z]", v):
            errors.append("huruf besar (A-Z)")
        if not re.search(r"[a-z]", v):
            errors.append("huruf kecil (a-z)")
        if not re.search(r"[^A-Za-z0-9]", v):
            errors.append("simbol (!@#$...)")
        if errors:
            raise ValueError("Password harus mengandung: " + ", ".join(errors))
        return v


class SignInRequest(BaseModel):
    email: EmailStr
    password: str


@router.post("/signup")
async def sign_up(body: SignUpRequest):
    """Self-service signup is closed: AutoScene is invite-only (Faith, 2026-08-03).

    Accounts are created by the admin through POST /admin/invites, which sends a
    Supabase invitation so the invitee sets their own password. The endpoint is
    kept (rather than deleted) so an old client gets a clear explanation instead
    of a 404.
    """
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="AutoScene is invite-only. Ask the administrator for an invitation.",
    )


@router.post("/me")
async def get_me(user_id: str):
    """Frontend uses Supabase client-side auth — this is a server-side helper."""
    client = get_supabase_client()
    result = client.table("users").select("*").eq("id", user_id).single().execute()
    if not result.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return result.data
