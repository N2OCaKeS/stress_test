"""Маскировка секретов в audit-payload перед отправкой в loging_service.

Идея: рекурсивно проходим по dict/list и заменяем подозрительные значения
типизированными плейсхолдерами:

- `<PASSWORD>`   — ключи password/pwd/pass/old_password/new_password
- `<TOKEN>`      — token/jwt/bearer/access_token/refresh_token/oauth_token/id_token
- `<SECRET>`     — secret/secret_key/api_key/apikey/client_secret/private_key
- `<HASH>`       — password_hash/hash/token_hash
- `<CREDENTIAL>` — credential/credentials/auth

Дополнительно — value-level эвристика:
  JWT-подобные строки (три base64-сегмента через `.`) → `<TOKEN>`
  bcrypt/argon2-хэши (`$2b$…`, `$argon2id$…`)         → `<HASH>`
  Опаковые токены платформы (`dbos_pat_…`, `dbos_bot_…`) → `<TOKEN>`

Маскировка идёт по обоим путям: и по имени ключа, и по форме значения.
Если ключ уже подразумевает один тип — он имеет приоритет.
"""

from __future__ import annotations

import re
from typing import Any

# ── Классификация ключей ─────────────────────────────────────────────────────

_PASSWORD_KEYS = {
    "password", "passwd", "pwd", "pass",
    "old_password", "new_password", "current_password",
    "confirm_password", "user_password",
    "bootstrap_password", "bootstrap_login",
    "password_plaintext",
}
_TOKEN_KEYS = {
    "token", "access_token", "refresh_token", "id_token",
    "oauth_token", "bearer", "jwt", "jwt_token",
    "refresh_token_hash",
    "session_token",
}
_SECRET_KEYS = {
    "secret", "secret_key", "api_key", "apikey",
    "client_secret", "private_key", "signing_key",
    "server_encryption_key", "master_key", "encryption_key",
    "hkdf_salt", "hkdf_salt_hex",
    "service_api_key", "logging_service_api_key",
    "ssh_private_key", "ssh_private_key_plaintext",
    # ssh_public_key — формально не секрет, но PII-adjacent: однозначно
    # идентифицирует actor'а. Маскируем в audit-details, чтобы не утекало в
    # SIEM в открытом виде.
    "ssh_public_key",
    # creds_stash_key / bootstrap_creds_key — ссылки на Redis-stash, не
    # сам секрет. Маскируем как defense-in-depth: если кто-то по ошибке
    # положит ссылку в audit-details через произвольный путь, она не
    # утечёт в SIEM (нельзя по ней притянуть plaintext — TTL короткий —
    # но ссылка раскрывает factual схему хранения, прячем её).
    "creds_stash_key",
    "bootstrap_creds_key",
}
_HASH_KEYS = {
    "password_hash", "hash", "token_hash", "pwd_hash",
}
_CREDENTIAL_KEYS = {
    "credential", "credentials", "auth", "authorization",
}

# ── Эвристики по значению ─────────────────────────────────────────────────────

_JWT_RE = re.compile(r"^[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}$")
_BCRYPT_RE = re.compile(r"^\$2[aby]?\$\d{1,2}\$[./A-Za-z0-9]{53}$")
_ARGON2_RE = re.compile(r"^\$argon2(id|i|d)?\$")
_OPAQUE_TOKEN_RE = re.compile(r"^dbos_(pat|bot)_[A-Za-z0-9_\-]{12,}$")

_MAX_STRING_LEN = 2048


def _classify_key(key: str) -> str | None:
    """Подобрать плейсхолдер по имени ключа (lowercased)."""
    k = key.lower()
    if k in _PASSWORD_KEYS:
        return "<PASSWORD>"
    if k in _TOKEN_KEYS:
        return "<TOKEN>"
    if k in _SECRET_KEYS:
        return "<SECRET>"
    if k in _HASH_KEYS:
        return "<HASH>"
    if k in _CREDENTIAL_KEYS:
        return "<CREDENTIAL>"
    return None


def _classify_value(value: str) -> str | None:
    """Подобрать плейсхолдер по форме значения (JWT/bcrypt/opaque-token)."""
    if _JWT_RE.match(value):
        return "<TOKEN>"
    if _OPAQUE_TOKEN_RE.match(value):
        return "<TOKEN>"
    if _BCRYPT_RE.match(value) or _ARGON2_RE.match(value):
        return "<HASH>"
    return None


def _redact_value(value: Any, key_placeholder: str | None) -> Any:
    """Решить, чем заменить отдельное значение. Длинные строки усекаются."""
    if key_placeholder is not None:
        return key_placeholder
    if isinstance(value, str):
        v_holder = _classify_value(value)
        if v_holder is not None:
            return v_holder
        if len(value) > _MAX_STRING_LEN:
            return value[:_MAX_STRING_LEN] + "…<TRUNCATED>"
    return value


def redact(payload: Any) -> Any:
    """Рекурсивно санитизирует dict/list. Не мутирует вход — возвращает новый объект."""
    if isinstance(payload, dict):
        out: dict[str, Any] = {}
        for k, v in payload.items():
            key_str = str(k)
            holder = _classify_key(key_str)
            if isinstance(v, (dict, list)):
                if holder is not None:
                    out[key_str] = holder
                else:
                    out[key_str] = redact(v)
            else:
                out[key_str] = _redact_value(v, holder)
        return out
    if isinstance(payload, list):
        return [
            redact(item) if isinstance(item, (dict, list))
            else _redact_value(item, None)
            for item in payload
        ]
    return payload
