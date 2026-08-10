"""Token encryption and session helpers.

Tokens (Upstox access/refresh) are encrypted at rest using AES-256-GCM via
the Fernet symmetric encryption scheme (cryptography library).  The
TOKEN_ENCRYPTION_KEY env var holds a URL-safe base64-encoded 32-byte key.

Session cookies store only an opaque session_id; all state lives in Redis.
Cookie values are HMAC-signed by itsdangerous so tampering is detectable.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timezone

from cryptography.fernet import Fernet, InvalidToken
from itsdangerous import BadSignature, SignatureExpired, TimestampSigner

from app.core.config import get_settings


# ── Token encryption ──────────────────────────────────────────────────────────

def _get_fernet() -> Fernet:
    settings = get_settings()
    return Fernet(settings.token_encryption_key.encode())


def encrypt_token(plaintext: str) -> bytes:
    """Encrypt an Upstox token for storage. Returns ciphertext bytes."""
    return _get_fernet().encrypt(plaintext.encode())


def decrypt_token(ciphertext: bytes) -> str:
    """Decrypt stored ciphertext. Raises ValueError on tampering/key mismatch."""
    try:
        return _get_fernet().decrypt(ciphertext).decode()
    except InvalidToken as exc:
        raise ValueError("Token decryption failed") from exc


# ── Session cookies ───────────────────────────────────────────────────────────

def _get_signer() -> TimestampSigner:
    settings = get_settings()
    return TimestampSigner(settings.session_secret_key)


def generate_session_id() -> str:
    """Return a cryptographically random 32-byte hex session ID."""
    return secrets.token_hex(32)


def sign_session_id(session_id: str) -> str:
    """HMAC-sign a session ID for use as a cookie value."""
    return _get_signer().sign(session_id).decode()


def unsign_session_id(signed: str) -> str | None:
    """Verify the HMAC signature and return the session_id or None."""
    settings = get_settings()
    try:
        raw = _get_signer().unsign(
            signed, max_age=settings.session_ttl_seconds
        )
        return raw.decode()
    except (BadSignature, SignatureExpired):
        return None


# ── OAuth state tokens ────────────────────────────────────────────────────────

def generate_oauth_state() -> str:
    """Generate a cryptographically random OAuth state token."""
    return secrets.token_urlsafe(32)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
