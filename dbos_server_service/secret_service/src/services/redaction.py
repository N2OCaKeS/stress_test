"""Маскировка секретов в audit-payload перед отправкой в loging_service.

Локальная копия для secret_service. Маскирует домен-специфичные ключи
этого сервиса: `login`, `secret`, `secret_b64`, `secret_encrypted`,
`master_key*`, `acl_dump` плюс общий пул (`password`, `token`, …).

Идея: рекурсивно (через явный стек) обходим dict/list и заменяем
подозрительные значения типизированными плейсхолдерами:

- `<PASSWORD>`   — password/pwd/pass/login/secret_b64/old_password/...
- `<TOKEN>`      — token/jwt/bearer/access_token/refresh_token/...
- `<SECRET>`     — secret/secret_encrypted/secret_key/api_key/client_secret/
                    private_key/master_key*/hkdf_salt*/...
- `<HASH>`       — password_hash/token_hash/hash
- `<CREDENTIAL>` — credential/credentials/auth/acl_dump

Дополнительно — value-level эвристика:
  JWT-подобные строки (три base64-сегмента через `.`)        → `<TOKEN>`
  bcrypt/argon2-хэши (`$2b$…`, `$argon2id$…`)                → `<HASH>`
  Опаковые токены платформы (`dbos_pat_…`, `dbos_bot_…`)     → `<TOKEN>`
  OAuth client_secret формы `cs_<token_urlsafe(≥12)>`        → `<SECRET>`
"""

from __future__ import annotations

import re
from typing import Any

# ── Классификация ключей ─────────────────────────────────────────────────────

# `login` — это PII (имя пользователя в внешней системе), плюс часть пары
# credential'а; в audit-details ему делать нечего. Маскируем как PASSWORD,
# чтобы фигурировал тем же типом placeholder'а, что и пара.
_PASSWORD_KEYS = {
    "password", "passwd", "pwd", "pass",
    "old_password", "new_password", "current_password",
    "confirm_password", "user_password",
    "login",
    "secret_b64",
}
_TOKEN_KEYS = {
    "token", "access_token", "refresh_token", "id_token",
    "oauth_token", "bearer", "bearer_token", "jwt", "jwt_token",
    "session_token",
}
_SECRET_KEYS = {
    "secret", "secret_key", "secret_encrypted",
    "api_key", "apikey", "api_secret",
    "client_secret", "private_key", "signing_key",
    "encryption_key",
    "master_key", "master_key_hex", "master_key_b64", "master_key_plaintext",
    "hkdf_salt", "hkdf_salt_hex", "hkdf_salt_b64",
    "service_api_key", "service_key", "introspect_key",
    "logging_service_api_key",
    "ssh_private_key", "ssh_private_key_plaintext",
    "ssh_public_key",
}
_HASH_KEYS = {
    "password_hash", "hash", "token_hash", "pwd_hash",
    "refresh_token_hash",
}
# `acl_dump` — снимок матрицы доступа (per-cred ACL'и + dept_grants). Может
# раскрывать, какие dep'ы видят какую креду; держим как credential-блок.
#
# `name` — человекочитаемое имя кред'ы (`tokens.create/update/delete/...`
# кладут его в details). Само по себе не секрет, но это metadata-leak вида
# «секрет <name> существует» в SOC-канал; маскируем как credential-блок.
_CREDENTIAL_KEYS = {
    "credential", "credentials", "auth", "authorization",
    "acl_dump",
    "name",
}

# ── Эвристики по значению ─────────────────────────────────────────────────────

_JWT_RE = re.compile(r"^[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}$")
_BCRYPT_RE = re.compile(r"^\$2[aby]?\$\d{1,2}\$[./A-Za-z0-9]{50,60}$")
_ARGON2_RE = re.compile(r"^\$argon2(id|i|d)?\$")
_OPAQUE_TOKEN_RE = re.compile(r"^dbos_(pat|bot)_[A-Za-z0-9_\-]{12,}$")
_OAUTH_CLIENT_SECRET_RE = re.compile(r"^cs_[A-Za-z0-9_\-]{12,}$")

_MAX_STRING_LEN = 2048

# Cap на глубину обхода — без него рекурсия по cyclic-friendly nested структуре
# уходит в линейный рост памяти.
_MAX_DEPTH = 64


def _classify_key(key: str) -> str | None:
    """Подобрать плейсхолдер по имени ключа (lowercased).

    HASH-проверка идёт перед TOKEN-проверкой: имена вида `refresh_token_hash`
    должны маскироваться как `<HASH>`, а не `<TOKEN>`.
    """
    k = key.lower()
    if k in _PASSWORD_KEYS:
        return "<PASSWORD>"
    if k in _HASH_KEYS:
        return "<HASH>"
    if k in _TOKEN_KEYS:
        return "<TOKEN>"
    if k in _SECRET_KEYS:
        return "<SECRET>"
    if k in _CREDENTIAL_KEYS:
        return "<CREDENTIAL>"
    return None


def _classify_value(value: str) -> str | None:
    """Подобрать плейсхолдер по форме значения (JWT/bcrypt/opaque-token/OAuth-secret)."""
    if _JWT_RE.match(value):
        return "<TOKEN>"
    if _OPAQUE_TOKEN_RE.match(value):
        return "<TOKEN>"
    if _OAUTH_CLIENT_SECRET_RE.match(value):
        return "<SECRET>"
    if _BCRYPT_RE.match(value) or _ARGON2_RE.match(value):
        return "<HASH>"
    return None


def _redact_value(value: Any, key_placeholder: str | None) -> Any:
    """Решить, чем заменить отдельное значение. Длинные строки усекаются."""
    if key_placeholder is not None:
        return key_placeholder
    if isinstance(value, (bytes, bytearray)):
        value = bytes(value).decode("utf-8", errors="replace")
    if isinstance(value, str):
        v_holder = _classify_value(value)
        if v_holder is not None:
            return v_holder
        if len(value) > _MAX_STRING_LEN:
            return value[:_MAX_STRING_LEN] + "…<TRUNCATED>"
    return value


def redact(payload: Any) -> Any:
    """Санитизирует dict/list без рекурсии. Не мутирует вход — возвращает новый объект.

    Обход — итеративный через explicit-стек. На глубине > `_MAX_DEPTH` (64)
    подставляем `"<TRUNCATED>"`. Top-level скаляр возвращается как есть.
    """
    if not isinstance(payload, (dict, list)):
        return payload

    root: list[Any] = [None]
    stack: list[tuple[Any, Any, Any, int]] = [(payload, root, 0, 0)]

    while stack:
        src, dst, key, depth = stack.pop()

        if depth > _MAX_DEPTH:
            dst[key] = "<TRUNCATED>"
            continue

        if isinstance(src, dict):
            new_dict: dict[str, Any] = {}
            dst[key] = new_dict
            for k, v in src.items():
                k_str = str(k)
                holder = _classify_key(k_str)
                if isinstance(v, (dict, list)):
                    if holder is not None:
                        new_dict[k_str] = holder
                    else:
                        stack.append((v, new_dict, k_str, depth + 1))
                else:
                    new_dict[k_str] = _redact_value(v, holder)
        elif isinstance(src, list):
            new_list: list[Any] = [None] * len(src)
            dst[key] = new_list
            for i, item in enumerate(src):
                if isinstance(item, (dict, list)):
                    stack.append((item, new_list, i, depth + 1))
                else:
                    new_list[i] = _redact_value(item, None)
        else:
            dst[key] = src

    return root[0]


def redact_payload(payload: dict) -> dict:
    """Удобный alias для caller'ов, ожидающих dict in, dict out."""
    result = redact(payload)
    return result if isinstance(result, dict) else {}
