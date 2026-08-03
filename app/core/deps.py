from typing import Annotated
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
import jwt as pyjwt
from jwt.algorithms import ECAlgorithm
import json
import logging
import httpx

from app.core.config import Settings, get_settings
from app.services.supabase import get_supabase_client

logger = logging.getLogger(__name__)
bearer_scheme = HTTPBearer()

# JWKS cache — populated once on first use
_jwks_cache: dict[str, object] = {}  # kid → public key object


def _load_jwks(supabase_url: str) -> None:
    """Fetch JWKS from Supabase and populate the cache."""
    global _jwks_cache
    jwks_url = f"{supabase_url}/auth/v1/.well-known/jwks.json"
    response = httpx.get(jwks_url, timeout=10)
    response.raise_for_status()
    data = response.json()
    for key_data in data.get("keys", []):
        kid = key_data.get("kid", "default")
        public_key = ECAlgorithm.from_jwk(json.dumps(key_data))
        _jwks_cache[kid] = public_key
    logger.info("JWKS loaded: %d key(s)", len(_jwks_cache))


def _get_public_key(kid: str | None, supabase_url: str):
    """Return the public key for the given kid, loading JWKS if needed."""
    if not _jwks_cache:
        _load_jwks(supabase_url)
    if kid and kid in _jwks_cache:
        return _jwks_cache[kid]
    # Fallback: first available key
    if _jwks_cache:
        return next(iter(_jwks_cache.values()))
    return None


async def get_current_user_id(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(bearer_scheme)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> str:
    """Validate Supabase JWT using JWKS public key (supports ES256)."""
    token = credentials.credentials
    try:
        header = pyjwt.get_unverified_header(token)
        alg = header.get("alg", "ES256")
        kid = header.get("kid")

        if alg == "HS256":
            # Legacy HS256 — verify with raw JWT secret
            payload = pyjwt.decode(
                token,
                settings.supabase_jwt_secret,
                algorithms=["HS256"],
                options={"verify_aud": False},
            )
        else:
            # ES256 (or other asymmetric) — verify with JWKS public key
            public_key = _get_public_key(kid, settings.supabase_url)
            if public_key is None:
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Could not load public key")
            payload = pyjwt.decode(
                token,
                public_key,
                algorithms=["ES256"],
                options={"verify_aud": False},
            )

        user_id: str | None = payload.get("sub")
        if not user_id:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
        return user_id

    except pyjwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired")
    except pyjwt.InvalidTokenError as e:
        logger.error("JWT invalid: %s", e)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    except HTTPException:
        raise
    except Exception as e:
        logger.error("JWT error: %s", e)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")


async def get_current_user(
    user_id: Annotated[str, Depends(get_current_user_id)],
) -> dict:
    """Fetch full user row from Supabase users table."""
    client = get_supabase_client()
    result = client.table("users").select("*").eq("id", user_id).single().execute()
    if not result.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return result.data


# Type aliases for DI
CurrentUserId = Annotated[str, Depends(get_current_user_id)]
CurrentUser   = Annotated[dict, Depends(get_current_user)]
AppSettings   = Annotated[Settings, Depends(get_settings)]


async def require_admin(user: Annotated[dict, Depends(get_current_user)]) -> dict:
    """Gate a route to admins. Faith's account is the only admin (see the
    2026-08-03 invite-only migration); everyone else is a plain user."""
    if (user.get("role") or "user") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admins only.",
        )
    return user


CurrentAdmin = Annotated[dict, Depends(require_admin)]
