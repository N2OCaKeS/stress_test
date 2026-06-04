# SOURCE OF TRUTH: dbos_server_service/sdk/redaction.py
# DUPE: keep in sync with sdk/redaction.py, auth_service/src/services/redaction.py,
#       loging_service/src/utils/redaction.py.
"""Маскировка секретов в audit-payload перед отправкой в loging_service.

Локальная копия для server_service. Содержит дополнительные ключи, специфичные
для домена серверов: bootstrap-кред prepare'а, мастер-ключи шифрования,
HKDF-соль, SSH-ключи, provision-payload (`password_plaintext`,
`ssh_private_key_plaintext`) и ссылки на Redis-stash (`creds_stash_key`,
`bootstrap_creds_key`). Все они едут отдельным каналом, но если попадут в
audit-details через debug/error-emit — sanitizer обязан их замаскировать,
иначе SIEM-оператор увидит plaintext.

Идея: рекурсивно проходим по dict/list и заменяем подозрительные значения
типизированными плейсхолдерами:

- `<PASSWORD>`   — ключи password/pwd/pass/bootstrap_password/password_plaintext/…
- `<TOKEN>`      — token/jwt/bearer/access_token/refresh_token/oauth_token/id_token
- `<SECRET>`     — secret/secret_key/api_key/apikey/client_secret/private_key/
                    master_key*/hkdf_salt*/ssh_private_key*/creds_stash_key/…
- `<HASH>`       — password_hash/hash/token_hash
- `<CREDENTIAL>` — credential/credentials/auth

Дополнительно — value-level эвристика:
  JWT-подобные строки (три base64-сегмента через `.`)        → `<TOKEN>`
  bcrypt/argon2-хэши (`$2b$…`, `$argon2id$…`)                → `<HASH>`
  Опаковые токены платформы (`dbos_pat_…`, `dbos_bot_…`)     → `<TOKEN>`
  OAuth client_secret формы `cs_<token_urlsafe(≥12)>`        → `<SECRET>`

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
    "oauth_token", "bearer", "bearer_token", "jwt", "jwt_token",
    "session_token",
}
_SECRET_KEYS = {
    "secret", "secret_key", "api_key", "apikey", "api_secret",
    "client_secret", "private_key", "signing_key",
    "server_encryption_key", "master_key", "encryption_key",
    "master_key_hex", "master_key_b64", "master_key_plaintext",
    "hkdf_salt", "hkdf_salt_hex", "hkdf_salt_b64",
    "service_api_key", "service_key", "introspect_key",
    "logging_service_api_key",
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
    "refresh_token_hash",
}
_CREDENTIAL_KEYS = {
    "credential", "credentials", "auth", "authorization",
}

# ── Эвристики по значению ─────────────────────────────────────────────────────

_JWT_RE = re.compile(r"^[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}$")
_BCRYPT_RE = re.compile(r"^\$2[aby]?\$\d{1,2}\$[./A-Za-z0-9]{50,60}$")
_ARGON2_RE = re.compile(r"^\$argon2(id|i|d)?\$")
_OPAQUE_TOKEN_RE = re.compile(r"^dbos_(pat|bot)_[A-Za-z0-9_\-]{12,}$")
_OAUTH_CLIENT_SECRET_RE = re.compile(r"^cs_[A-Za-z0-9_\-]{12,}$")

_MAX_STRING_LEN = 2048

# Жёсткий cap на глубину обхода. Provision-payload / worker-task-payload
# приходит произвольной глубины; без верхнего предела итерация уходит в
# линейный рост памяти на cyclic-friendly nested структуре.
_MAX_DEPTH = 64


def _classify_key(key: str) -> str | None:
    """Подобрать плейсхолдер по имени ключа (lowercased).

    HASH-проверка идёт перед TOKEN-проверкой: имена вида `refresh_token_hash`
    должны маскироваться как `<HASH>`, а не `<TOKEN>` (хэш токена ≠ токен).
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
        # bytes под безопасным ключом декодируем permissively через
        # `errors="replace"`: иначе JSON-сериализатор уронил бы `b'...'`-репр
        # в БД, минуя classify_value (regex'ы работают по str без `b'`-префикса).
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

    Обход — итеративный через explicit-стек: на глубоком payload'е рекурсивная
    версия упиралась в `RecursionError` (limit ≈ 1000) ещё до того, как
    срабатывали upstream-каппы на размер тела. На глубине > `_MAX_DEPTH` (64)
    подставляем `"<TRUNCATED>"` плейсхолдер.

    Контракт — только для контейнеров: маскировка строк работает исключительно
    для значений ВНУТРИ dict/list (по имени ключа или pattern'у на content).
    Top-level скаляр возвращается как есть.
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
