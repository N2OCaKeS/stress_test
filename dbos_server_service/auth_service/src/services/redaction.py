# SOURCE OF TRUTH: dbos_server_service/sdk/redaction.py
# DUPE: keep in sync with sdk/redaction.py, loging_service/src/utils/redaction.py,
#       server_service/src/services/redaction.py.
"""Маскировка секретов в audit-payload перед отправкой в loging_service.

Локальная копия для auth_service. Сервисные тесты фиксируют:
  * приоритет в `_classify_key`: PASSWORD → HASH → TOKEN → SECRET → CREDENTIAL
    (HASH перед TOKEN — `refresh_token_hash`/`pat_hash`/`bot_token_hash` должны
    маскироваться как `<HASH>`, не `<TOKEN>`);
  * `cs_<...>` OAuth client_secret → `<SECRET>` по значению;
  * `token_plaintext`/`pat_token`/`bot_token` в `_TOKEN_KEYS`;
  * расширенное окно bcrypt-хвоста 50..60 (усечённые/padded версии).

Идея: рекурсивно проходим по dict/list и заменяем подозрительные значения
типизированными плейсхолдерами:

- `<PASSWORD>`   — ключи password/pwd/pass/old_password/new_password/…
- `<TOKEN>`      — token/jwt/bearer/access_token/refresh_token/oauth_token/id_token/…
- `<SECRET>`     — secret/secret_key/api_key/apikey/client_secret/private_key/…
- `<HASH>`       — password_hash/hash/token_hash/pwd_hash/refresh_token_hash/pat_hash/bot_token_hash
- `<CREDENTIAL>` — credential/credentials/auth/authorization

Дополнительно — value-level эвристика:
  JWT-подобные строки (три base64-сегмента через `.`)        → `<TOKEN>`
  Опаковые токены платформы (`dbos_pat_…`, `dbos_bot_…`)     → `<TOKEN>`
  OAuth client_secret формы `cs_<token_urlsafe(≥12)>`        → `<SECRET>`
  bcrypt/argon2-хэши (`$2b$…`, `$argon2id$…`)                → `<HASH>`
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
    "oauth_token", "bearer", "bearer_token", "jwt", "jwt_token",
    "session_token",
    # defense-in-depth: если кто-то решит положить plaintext в явно
    # названный ключ — маскируем по имени, не дожидаясь эвристики по dbos_*
    "token_plaintext", "pat_token", "bot_token",
}
_SECRET_KEYS = {
    "secret", "secret_key", "api_key", "apikey", "api_secret",
    "client_secret", "private_key", "signing_key",
    "service_api_key", "service_key", "introspect_key",
    "logging_service_api_key",
}
_HASH_KEYS = {
    # Хэши секретов — маскируем. Generic `hash` оставлен для обратной
    # совместимости с hypothesis-тестом (см. test_redaction_hypothesis); если
    # появятся легитимные ключи вроде `git_commit_hash`, переименуй их в коде.
    "hash", "password_hash", "token_hash", "pwd_hash",
    "refresh_token_hash", "pat_hash", "bot_token_hash",
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
# OAuth client secret: cs_<token_ursafe(32)>. Маскируем по форме, чтобы plaintext
# не утёк, даже если попадёт в audit-details под ключом без явного `secret`-имени
# (например `raw_secret`, `payload`).
_OAUTH_CLIENT_SECRET_RE = re.compile(r"^cs_[A-Za-z0-9_\-]{12,}$")

# Длинные hex-строки/base64 без явного ключа обычно не маскируем — слишком много
# ложноположительных. Делается только по контексту ключа.

_MAX_STRING_LEN = 2048  # очень длинные строки усекаются, маркер `<TRUNCATED>` добавляется в конец

# Жёсткий cap на глубину обхода. Caller'ы (например worker payload) могут
# прислать произвольно глубокий dict; без верхнего предела итерация уходит в
# линейный рост памяти на cyclic-friendly nested структуре.
_MAX_DEPTH = 64


def _classify_key(key: str) -> str | None:
    """Возвращает плейсхолдер по имени ключа, либо None если ключ безопасен.

    _HASH_KEYS проверяем РАНЬШЕ _TOKEN_KEYS: имена вида `refresh_token_hash` /
    `pat_hash` / `bot_token_hash` относятся к хэшам токенов (метка `<HASH>`),
    не к токенам как таковым.
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
    """Эвристика по форме значения."""
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
    """Применяет маскировку к скалярному значению (или возвращает как есть)."""
    if key_placeholder is not None:
        # Ключ диктует тип — всегда маскируем, независимо от значения
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

    Применение:
        details = redact({"reason": "invalid", "password": "p@ss"})
        # → {"reason": "invalid", "password": "<PASSWORD>"}
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
