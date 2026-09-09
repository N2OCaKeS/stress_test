"""Маскировка секретов в audit-payload перед отправкой в loging_service.

Локальная копия общего паттерна (secret_service/server_service/server_worker).
Набор ключей — общий пул, без секрето-специфичных полей (`master_key`,
`hkdf_salt` и т.п. остаются зоной secret_service). `ssh_private_key`/
`ssh_public_key`/`test_password` заведены заранее — testing_service будет
резолвить `TEST_PASSWORD`/`TEST_SSH_KEY` из §2.1 плана миграции в волне 5,
маскировка нужна сразу, чтобы не тащить эти поля plaintext в audit-канал
случайно, как только появится первый вызывающий код.
"""

from __future__ import annotations

import re
from typing import Any

_PASSWORD_KEYS = {
    "password", "passwd", "pwd", "pass",
    "old_password", "new_password", "current_password",
    "confirm_password", "user_password",
    "login", "test_password",
}
_TOKEN_KEYS = {
    "token", "access_token", "refresh_token", "id_token",
    "oauth_token", "bearer", "bearer_token", "jwt", "jwt_token",
    "session_token",
}
_SECRET_KEYS = {
    "secret", "secret_key",
    "api_key", "apikey", "api_secret",
    "client_secret", "private_key", "signing_key",
    "service_api_key", "service_key", "introspect_key",
    "logging_service_api_key",
    "ssh_private_key", "ssh_private_key_plaintext",
    "ssh_public_key",
}
_HASH_KEYS = {
    "password_hash", "hash", "token_hash", "pwd_hash",
    "refresh_token_hash",
}
_CREDENTIAL_KEYS = {
    "credential", "credentials", "auth", "authorization",
}

_JWT_RE = re.compile(r"^[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}$")
_BCRYPT_RE = re.compile(r"^\$2[aby]?\$\d{1,2}\$[./A-Za-z0-9]{50,60}$")
_ARGON2_RE = re.compile(r"^\$argon2(id|i|d)?\$")
_OPAQUE_TOKEN_RE = re.compile(r"^dbos_(pat|bot)_[A-Za-z0-9_\-]{12,}$")

_MAX_STRING_LEN = 2048
_MAX_DEPTH = 64


def _classify_key(key: str) -> str | None:
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
    if _JWT_RE.match(value):
        return "<TOKEN>"
    if _OPAQUE_TOKEN_RE.match(value):
        return "<TOKEN>"
    if _BCRYPT_RE.match(value) or _ARGON2_RE.match(value):
        return "<HASH>"
    return None


def _redact_value(value: Any, key_placeholder: str | None) -> Any:
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
    """Санитизирует dict/list без рекурсии. Не мутирует вход — возвращает новый объект."""
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
