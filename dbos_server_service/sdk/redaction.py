"""Маскировка секретов в dict/list — для audit-payload и подобного.

Канонический источник для копи-паста между сервисами. Эталон — версия из
`loging_service/src/utils/redaction.py` (самая консервативная: ключ-
классификатор схлопывает любой контейнер на плейсхолдер, даже если внутри
формально безопасные поля).

Где сейчас дублируется этот код:
    auth_service/src/services/redaction.py     — почти 1:1, чуть более «мягкая»
                                                  логика для dict-значений
    loging_service/src/utils/redaction.py      — эталон (берётся сюда)
    server_service/src/services/redaction.py   — почти 1:1 с loging-версией

Отдельно: `server_worker/src/utils/redaction.py` — **другой API**, маскирует
free-form строки (URL-credentials, ipmitool-args). Этот SDK-файл его НЕ
заменяет; в `server_worker` копировать не нужно.

Алгоритм:

1. Рекурсивно обходим dict/list. На скалярах применяем эвристику.
2. Имя ключа классифицируется по словарям `_PASSWORD_KEYS` / `_TOKEN_KEYS` /
   `_SECRET_KEYS` / `_HASH_KEYS` / `_CREDENTIAL_KEYS`. Match → плейсхолдер
   подставляется ВМЕСТО значения, независимо от его формы и типа (даже
   если значение — dict/list, оно схлопывается).
3. Если ключ безопасный, но значение — строка похожая на JWT
   (`a.b.c` base64), opaque-token (`dbos_pat_…` / `dbos_bot_…`) или
   bcrypt/argon2 хэш → подставляем соответствующий плейсхолдер.
4. Длинные строки (>2048 символов) усекаются с маркером `<TRUNCATED>`.

Плейсхолдеры: `<PASSWORD>`, `<TOKEN>`, `<SECRET>`, `<HASH>`, `<CREDENTIAL>`.

Использование:

    from sdk_local.redaction import redact
    safe_details = redact({"username": "admin", "password": "hunter2"})
    # → {"username": "admin", "password": "<PASSWORD>"}
"""

from __future__ import annotations

import re
from typing import Any

_PASSWORD_KEYS = {
    "password", "passwd", "pwd", "pass",
    "old_password", "new_password", "current_password",
    "confirm_password", "user_password",
}
_TOKEN_KEYS = {
    "token", "access_token", "refresh_token", "id_token",
    "oauth_token", "bearer", "jwt", "jwt_token",
    "refresh_token_hash", "session_token",
}
_SECRET_KEYS = {
    "secret", "secret_key", "api_key", "apikey",
    "client_secret", "private_key", "signing_key",
}
_HASH_KEYS = {
    "password_hash", "hash", "token_hash", "pwd_hash",
}
_CREDENTIAL_KEYS = {
    "credential", "credentials", "auth", "authorization",
}

_JWT_RE = re.compile(r"^[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}$")
_BCRYPT_RE = re.compile(r"^\$2[aby]?\$\d{1,2}\$[./A-Za-z0-9]{53}$")
_ARGON2_RE = re.compile(r"^\$argon2(id|i|d)?\$")
_OPAQUE_TOKEN_RE = re.compile(r"^dbos_(pat|bot)_[A-Za-z0-9_\-]{12,}$")

_MAX_STRING_LEN = 2048


def _classify_key(key: str) -> str | None:
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
    if isinstance(value, str):
        v_holder = _classify_value(value)
        if v_holder is not None:
            return v_holder
        if len(value) > _MAX_STRING_LEN:
            return value[:_MAX_STRING_LEN] + "…<TRUNCATED>"
    return value


def redact(payload: Any) -> Any:
    """Рекурсивно санитизирует dict/list. Не мутирует вход — возвращает новый объект.

    Контейнеры схлопываются на ключе-классификаторе: если `password` указывает
    на dict, весь dict превращается в `<PASSWORD>`. Это сознательная защита
    от обхода маскировки через вложение.
    """
    if isinstance(payload, dict):
        out: dict[str, Any] = {}
        for k, v in payload.items():
            key_str = str(k)
            holder = _classify_key(key_str)
            if isinstance(v, (dict, list)):
                out[key_str] = holder if holder is not None else redact(v)
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
