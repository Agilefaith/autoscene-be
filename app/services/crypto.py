"""At-rest encryption for user-supplied secrets (per-user voice API keys).

Fernet (AES-128-CBC + HMAC) via the `cryptography` package that already ships in
requirements.txt. The Fernet key comes from APP_ENCRYPTION_KEY; when that env is
unset, it is derived deterministically from SUPABASE_JWT_SECRET so existing
deployments keep working without a new required env var. Rotating either secret
invalidates stored ciphertexts — users would simply re-enter their API key.
"""

import base64
import hashlib
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    settings = get_settings()
    secret = settings.app_encryption_key or settings.supabase_jwt_secret
    if not secret:
        raise RuntimeError("APP_ENCRYPTION_KEY or SUPABASE_JWT_SECRET must be set for encryption")
    # Fernet needs a urlsafe-b64 32-byte key; derive one from the secret.
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())
    return Fernet(key)


def encrypt(plaintext: str) -> str:
    """Encrypt a secret for storage. Returns a urlsafe token string."""
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(token: str | None) -> str | None:
    """Decrypt a stored token; None/empty in → None out. An undecryptable token
    (rotated secret, corrupt row) also returns None so callers fall back to the
    platform key instead of crashing a render."""
    if not token:
        return None
    try:
        return _fernet().decrypt(token.encode()).decode()
    except (InvalidToken, ValueError):
        return None
