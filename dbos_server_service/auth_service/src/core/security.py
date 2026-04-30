"""Password hashing, JWT generation, and opaque token helpers."""

import hashlib
import secrets
from datetime import timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from src.core.config import get_settings
from src.core.constants import BOT_TOKEN_PREFIX, PAT_PREFIX

_ph = PasswordHasher()
_ALGORITHM = "HS256"


# ── Password ─────────────────────────────────────────────────────────────────

def hash_password(plain: str) -> str:
    return _ph.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return _ph.verify(hashed, plain)
    except (VerifyMismatchError, InvalidHashError):
        return False


# ── JWT access tokens ─────────────────────────────────────────────────────────

def create_access_token(payload: dict, expires_delta: timedelta | None = None) -> str:
    settings = get_settings()
    if expires_delta is None:
        expires_delta = timedelta(minutes=settings.access_token_ttl_minutes)

    import time
    data = payload.copy()
    now = int(time.time())
    data["iat"] = now
    data["exp"] = now + int(expires_delta.total_seconds())
    return jwt.encode(data, settings.secret_key, algorithm=_ALGORITHM)


def decode_access_token(token: str) -> dict:
    settings = get_settings()
    return jwt.decode(token, settings.secret_key, algorithms=[_ALGORITHM])


# ── Opaque refresh tokens ─────────────────────────────────────────────────────

def generate_refresh_token() -> tuple[str, str]:
    """Return (raw_token, sha256_hash). Only the hash is stored."""
    raw = secrets.token_urlsafe(48)
    return raw, _sha256(raw)


def hash_refresh_token(raw: str) -> str:
    return _sha256(raw)


# ── Personal access tokens ────────────────────────────────────────────────────

def generate_pat() -> tuple[str, str, str]:
    """Return (raw_token, prefix, sha256_hash)."""
    secret = secrets.token_urlsafe(32)
    raw = f"{PAT_PREFIX}{secret}"
    return raw, raw[:12], _sha256(raw)


# ── Bot tokens ────────────────────────────────────────────────────────────────

def generate_bot_token() -> tuple[str, str, str]:
    """Return (raw_token, prefix, sha256_hash)."""
    secret = secrets.token_urlsafe(32)
    raw = f"{BOT_TOKEN_PREFIX}{secret}"
    return raw, raw[:12], _sha256(raw)


def hash_opaque_token(raw: str) -> str:
    return _sha256(raw)


# ── Internal ──────────────────────────────────────────────────────────────────

def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()
