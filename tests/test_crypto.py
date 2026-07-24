"""At-rest encryption for per-user voice API keys (services/crypto.py)."""

from app.services import crypto


def test_encrypt_decrypt_roundtrip():
    secret = "sk-user-elevenlabs-key-123"
    token = crypto.encrypt(secret)
    assert token != secret
    assert crypto.decrypt(token) == secret


def test_decrypt_none_and_empty():
    assert crypto.decrypt(None) is None
    assert crypto.decrypt("") is None


def test_decrypt_garbage_returns_none():
    # A corrupt/rotated token must degrade to None (fall back to platform key),
    # never crash a render.
    assert crypto.decrypt("not-a-fernet-token") is None
