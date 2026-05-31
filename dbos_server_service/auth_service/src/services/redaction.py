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

Маскировка идёт по обоим путям: и по имени ключа, и по форме значения.
Если ключ уже подразумевает один тип — он имеет приоритет (например, ключ
`password` со значением, похожим на JWT, всё равно станет `<PASSWORD>`).
"""

from __future__ import annotations

import re
from typing import Any

# ── Классификация ключей ─────────────────────────────────────────────────────

_PASSWORD_KEYS = {
    "password", "passwd", "pwd", "pass",
    "old_password", "new_password", "current_password",
    "confirm_password", "user_password",
}
_TOKEN_KEYS = {
    "token", "access_token", "refresh_token", "id_token",
    "oauth_token", "bearer", "jwt", "jwt_token",
    "refresh_token_hash",  # тоже секрет, маскируем как TOKEN-уровень
    "session_token",
    # defense-in-depth: если кто-то решит положить plaintext в явно
    # названный ключ — маскируем по имени, не дожидаясь эвристики по dbos_*
    "token_plaintext", "pat_token", "bot_token",
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

# ── Эвристики по значению ─────────────────────────────────────────────────────

_JWT_RE = re.compile(r"^[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}$")
# Хвост bcrypt'а формально 53 символа (22 salt + 31 hash, base64-modified
# alphabet ./A-Za-z0-9). Разные библиотеки иногда дают 50-60 (обрезанный
# вывод, padding-вариации, версии $2x$). Чтобы sanitizer не пропустил
# нестандартный, но всё ещё узнаваемый hash в audit-details, расширяем
# диапазон. False-positive на 50+ char base64-like строку без префикса
# $2[aby]$ — практически невозможен (префикс жёсткий).
_BCRYPT_RE = re.compile(r"^\$2[aby]?\$\d{1,2}\$[./A-Za-z0-9]{50,60}$")
_ARGON2_RE = re.compile(r"^\$argon2(id|i|d)?\$")
# Опаковые токены системы: pat = dbos_pat_<random>, bot = dbos_bot_<random>
_OPAQUE_TOKEN_RE = re.compile(r"^dbos_(pat|bot)_[A-Za-z0-9_\-]{12,}$")

# Длинные hex-строки/base64 без явного ключа обычно не маскируем — слишком много
# ложноположительных. Делается только по контексту ключа.

_MAX_STRING_LEN = 2048  # очень длинные строки усекаются, маркер `<TRUNCATED>` добавляется в конец


def _classify_key(key: str) -> str | None:
    """Возвращает плейсхолдер по имени ключа, либо None если ключ безопасен."""
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
    """Эвристика по форме значения."""
    if _JWT_RE.match(value):
        return "<TOKEN>"
    if _OPAQUE_TOKEN_RE.match(value):
        return "<TOKEN>"
    if _BCRYPT_RE.match(value) or _ARGON2_RE.match(value):
        return "<HASH>"
    return None


def _redact_value(value: Any, key_placeholder: str | None) -> Any:
    """Применяет маскировку к скалярному значению (или возвращает как есть)."""
    if key_placeholder is not None:
        # Ключ диктует тип — всегда маскируем, независимо от значения
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

    Применение:
        details = redact({"reason": "invalid", "password": "p@ss"})
        # → {"reason": "invalid", "password": "<PASSWORD>"}
    """
    if isinstance(payload, dict):
        out: dict[str, Any] = {}
        for k, v in payload.items():
            key_str = str(k)
            holder = _classify_key(key_str)
            if isinstance(v, (dict, list)):
                # Контейнер: либо ключ диктует маскировку целиком, либо рекурсия
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
    # На верхнем уровне non-dict — возвращаем как есть
    return payload
