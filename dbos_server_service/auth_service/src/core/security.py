"""Security primitives: Argon2id хэширование, JWT mint/decode, opaque-токены (refresh/PAT/bot)."""

import hashlib
import secrets
from datetime import timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from src.core.config import get_settings
from src.core.constants import BOT_TOKEN_PREFIX, PAT_PREFIX, TOKEN_PREFIX_LEN

# ── Argon2id ──────────────────────────────────────────────────────────────────
#
# Параметры зафиксированы явно (OWASP 2023 recommendation для Argon2id):
#   * time_cost=3        — 3 итерации
#   * memory_cost=65536  — 64 MiB памяти на хеш
#   * parallelism=4      — 4 параллельных лейна
#   * hash_len=32        — 32 байта на выходе
#   * salt_len=16        — 16 байт соли
#
# Без явных параметров `PasswordHasher()` использует дефолты argon2-cffi,
# которые могут поменяться при обновлении библиотеки и тихо ослабить
# production-хеш. Явная фиксация делает поведение воспроизводимым.
_ph = PasswordHasher(
    time_cost=3,
    memory_cost=65536,
    parallelism=4,
    hash_len=32,
    salt_len=16,
)
_ALGORITHM = "HS256"


# ── Password ─────────────────────────────────────────────────────────────────

def hash_password(plain: str) -> str:
    """Argon2id-хэш пароля. ~100 мс на вычисление при OWASP-параметрах выше."""
    return _ph.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    """Проверить пароль против Argon2id-хэша. False при mismatch/invalid hash."""
    try:
        return _ph.verify(hashed, plain)
    except (VerifyMismatchError, InvalidHashError):
        return False


# ── JWT access tokens ─────────────────────────────────────────────────────────

def create_access_token(payload: dict, expires_delta: timedelta | None = None) -> str:
    """Сминтить подписанный JWT с claim'ами `iat`/`exp`/`iss`/`aud`.

    `iss` и `aud` берутся из settings (`JWT_ISSUER`, `JWT_AUDIENCE`) и
    проверяются на decode — это блокирует cross-audience replay. `iat` нужен
    под `require=["iat"]` на decode, а ещё помогает аналитике.
    """
    settings = get_settings()
    if expires_delta is None:
        expires_delta = timedelta(minutes=settings.access_token_ttl_minutes)

    import time
    data = payload.copy()
    now = int(time.time())
    data["iat"] = now
    data["exp"] = now + int(expires_delta.total_seconds())
    # Caller может явно перекрыть iss/aud (например, для тестов), но в
    # обычном потоке оба claim'а навязываются настройками — иначе токен не
    # пройдёт decode на нашей же стороне.
    data.setdefault("iss", settings.jwt_issuer)
    data.setdefault("aud", settings.jwt_audience)
    return jwt.encode(data, settings.secret_key, algorithm=_ALGORITHM)


def decode_access_token(token: str) -> dict:
    """Валидировать подпись + `exp`/`iat`/`iss`/`aud` + наличие обязательных claim'ов.

    Кидает `jwt.InvalidTokenError` (или подкласс) на любую проблему: bad
    signature, expired, missing iat/exp/sub, wrong issuer/audience, clock-skew
    выше `JWT_LEEWAY_SECONDS`.
    """
    settings = get_settings()
    return jwt.decode(
        token,
        settings.secret_key,
        algorithms=[_ALGORITHM],
        audience=settings.jwt_audience,
        issuer=settings.jwt_issuer,
        leeway=settings.jwt_leeway_seconds,
        options={
            "verify_aud": True,
            "verify_iss": True,
            "verify_exp": True,
            "verify_iat": True,
            "verify_signature": True,
            "require": ["exp", "iat", "sub"],
        },
    )


# ── Opaque refresh tokens ─────────────────────────────────────────────────────

def generate_refresh_token() -> tuple[str, str]:
    """Сгенерить refresh: возвращает `(raw, sha256_hash)`. В БД пишем только hash."""
    raw = secrets.token_urlsafe(48)
    return raw, _sha256(raw)


def hash_refresh_token(raw: str) -> str:
    """SHA-256 от raw refresh — для lookup'а в БД."""
    return _sha256(raw)


# ── Personal access tokens ────────────────────────────────────────────────────

def generate_pat() -> tuple[str, str, str]:
    """Сгенерить PAT. Возвращает `(raw, prefix, sha256_hash)`. Префикс — `dbos_pat_`."""
    secret = secrets.token_urlsafe(32)
    raw = f"{PAT_PREFIX}{secret}"
    return raw, raw[:TOKEN_PREFIX_LEN], _sha256(raw)


# ── Bot tokens ────────────────────────────────────────────────────────────────

def generate_bot_token() -> tuple[str, str, str]:
    """Сгенерить bot-токен. Возвращает `(raw, prefix, sha256_hash)`. Префикс — `dbos_bot_`."""
    secret = secrets.token_urlsafe(32)
    raw = f"{BOT_TOKEN_PREFIX}{secret}"
    return raw, raw[:TOKEN_PREFIX_LEN], _sha256(raw)


def hash_opaque_token(raw: str) -> str:
    """SHA-256 для любого opaque-токена (PAT / bot / OAuth client secret / authorization_code)."""
    return _sha256(raw)


def mask_email(email: str | None) -> str | None:
    """Замаскировать email для audit-details.

    Полный email — это PII; loging_reader не должен видеть его plaintext.
    Формат: первый символ local-part + `***@domain`. Если на входе мусор
    (без `@` или пустой local-part) — возвращаем `<EMAIL>` целиком, чтобы
    не утечь даже хвост.

        mask_email("john.doe@corp.local") == "j***@corp.local"
        mask_email("a@x")                  == "a***@x"
        mask_email("")                     is None
        mask_email("notanemail")           == "<EMAIL>"
    """
    if not email:
        return email
    if "@" not in email:
        return "<EMAIL>"
    local, _, domain = email.partition("@")
    if not local or not domain:
        return "<EMAIL>"
    return f"{local[0]}***@{domain}"


# ── Internal helpers ──────────────────────────────────────────────────────────

def _sha256(value: str) -> str:
    """SHA-256 hex digest. Один внутренний helper, чтобы не дублировать impl."""
    return hashlib.sha256(value.encode()).hexdigest()
