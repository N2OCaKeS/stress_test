"""Маскировка секретов в dict/list audit-payload — эталонный источник.

# SOURCE OF TRUTH: dbos_server_service/sdk/redaction.py
# DUPE: keep in sync with
#   - auth_service/src/services/redaction.py
#   - loging_service/src/utils/redaction.py
#   - server_service/src/services/redaction.py
# Каталог `sdk/` — не pip-пакет и не shared import: PYTHONPATH сервисов
# изолирован, общего модуля между ними нет. Каждый сервис держит свою копию
# этого файла; при правке здесь — синхронизируй вручную во все три места.
#
# `server_worker/src/utils/redaction.py` — отдельный модуль с другим API
# (free-form строки, не dict). Под этот SDK НЕ попадает.
#
# # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # #
# Различия между сервисными копиями — намеренные и привязаны к локальным
# тестам каждого сервиса:
#
# `_PASSWORD_KEYS`:
#   - server_service добавляет `bootstrap_login`, `bootstrap_password`,
#     `password_plaintext` (provision-payload, prepare worker dispatch).
#
# `_TOKEN_KEYS`:
#   - auth добавляет `token_plaintext`, `pat_token`, `bot_token` — выдача
#     PAT/bot-токенов с явным «plaintext» именем ключа в audit.
#   - loging держит `refresh_token_hash` ЗДЕСЬ (классифицируется как `<TOKEN>`).
#   - auth перенёс `refresh_token_hash`/`pat_hash`/`bot_token_hash` в
#     `_HASH_KEYS` (тест-зафиксированный приоритет: HASH-проверка идёт
#     ДО TOKEN-проверки в `_classify_key`).
#   - все три добавляют `bearer_token` — long-form bearer header.
#
# `_SECRET_KEYS`:
#   - все: `service_api_key`, `api_secret` — общий S2S-словарь.
#   - loging: `service_key`, `introspect_key` (rotate-audit с этими именами
#     приходит из auth и server, поэтому loging держит их в своём наборе).
#   - auth: `logging_service_api_key` — старое имя env-переменной в audit-rotate.
#   - server_service: `master_key*`, `encryption_key`, `hkdf_salt*`,
#     `ssh_private_key*`, `ssh_public_key` (PII-adjacent), `creds_stash_key`,
#     `bootstrap_creds_key`, `server_encryption_key`.
#
# `_HASH_KEYS`:
#   - база: `password_hash`, `hash`, `token_hash`, `pwd_hash`.
#   - auth: + `refresh_token_hash`, `pat_hash`, `bot_token_hash` (см. выше).
#
# Приоритет `_classify_key`:
#   - auth: PASSWORD → HASH → TOKEN → SECRET → CREDENTIAL. HASH перед TOKEN —
#     `refresh_token_hash` должен дать `<HASH>`, а не `<TOKEN>`.
#   - loging/server: PASSWORD → TOKEN → SECRET → HASH → CREDENTIAL — поведение
#     консервативно, тесты этих сервисов не имеют HASH-перед-TOKEN кейсов.
#
# Регэкспы (одинаковые во всех копиях):
#   - `_JWT_RE`         — три base64url-сегмента через `.`.
#   - `_BCRYPT_RE`      — `$2[aby]?$<cost>$` + 50..60 base64-modified символов
#                          (расширенное окно для усечённых/padded хвостов).
#   - `_ARGON2_RE`      — префикс `$argon2(id|i|d)?$`.
#   - `_OPAQUE_TOKEN_RE`— `dbos_(pat|bot)_<random≥12>`.
#   - `_OAUTH_CLIENT_SECRET_RE` — `cs_<token_urlsafe≥12>`.
#
# Реализация — итеративная с явным стеком и `_MAX_DEPTH=64` cap:
# рекурсивный обход упирался в `RecursionError` (CPython limit ≈ 1000) ещё
# до того, как срабатывали upstream-caps на размер тела audit-event.
# `bytes/bytearray` под безопасным ключом декодируются permissively через
# `errors="replace"`, чтобы JWT/bcrypt/argon2 в bytes-репрезентации не уехали
# в БД как `str(b'...')`.
# # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # #

Алгоритм:

1. Рекурсивно обходим dict/list (итеративно через стек). На скалярах
   применяем эвристику.
2. Имя ключа классифицируется по словарям `_PASSWORD_KEYS` / `_TOKEN_KEYS` /
   `_SECRET_KEYS` / `_HASH_KEYS` / `_CREDENTIAL_KEYS`. Match → плейсхолдер
   подставляется ВМЕСТО значения, независимо от его формы и типа (даже
   если значение — dict/list, оно схлопывается).
3. Если ключ безопасный, но значение — строка похожая на JWT
   (`a.b.c` base64), opaque-token (`dbos_pat_…` / `dbos_bot_…`), OAuth
   client_secret (`cs_…`) или bcrypt/argon2 хэш → подставляем
   соответствующий плейсхолдер.
4. Длинные строки (>2048 символов) усекаются с маркером `<TRUNCATED>`.
5. На глубине > `_MAX_DEPTH` (64) ветка обрезается плейсхолдером
   `<TRUNCATED>` — защита от pathological-вложенности.

Плейсхолдеры: `<PASSWORD>`, `<TOKEN>`, `<SECRET>`, `<HASH>`, `<CREDENTIAL>`.

Использование (внутри сервиса, через локальную копию):

    from src.services.redaction import redact   # auth/server
    # или: from src.utils.redaction import redact   # loging
    safe_details = redact({"username": "admin", "password": "hunter2"})
    # → {"username": "admin", "password": "<PASSWORD>"}
"""

from __future__ import annotations

import re
from typing import Any

# Объединение всех ключевых наборов, известных трём сервисам. Сервисная
# копия может держать СВОЙ набор (см. шапку); этот файл — справочный
# superset, чтобы не приходилось каждый раз поднимать историю по веткам.
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
    "token_plaintext", "pat_token", "bot_token",
}
_SECRET_KEYS = {
    "secret", "secret_key", "api_key", "apikey", "api_secret",
    "client_secret", "private_key", "signing_key",
    "service_api_key", "service_key", "introspect_key",
    "logging_service_api_key",
    "server_encryption_key", "master_key", "encryption_key",
    "master_key_hex", "master_key_b64", "master_key_plaintext",
    "hkdf_salt", "hkdf_salt_hex", "hkdf_salt_b64",
    "ssh_private_key", "ssh_private_key_plaintext", "ssh_public_key",
    "creds_stash_key", "bootstrap_creds_key",
}
_HASH_KEYS = {
    "password_hash", "hash", "token_hash", "pwd_hash",
    "refresh_token_hash", "pat_hash", "bot_token_hash",
}
_CREDENTIAL_KEYS = {
    "credential", "credentials", "auth", "authorization",
}

_JWT_RE = re.compile(r"^[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}$")
_BCRYPT_RE = re.compile(r"^\$2[aby]?\$\d{1,2}\$[./A-Za-z0-9]{50,60}$")
_ARGON2_RE = re.compile(r"^\$argon2(id|i|d)?\$")
_OPAQUE_TOKEN_RE = re.compile(r"^dbos_(pat|bot)_[A-Za-z0-9_\-]{12,}$")
_OAUTH_CLIENT_SECRET_RE = re.compile(r"^cs_[A-Za-z0-9_\-]{12,}$")

_MAX_STRING_LEN = 2048
_MAX_DEPTH = 64


def _classify_key(key: str) -> str | None:
    # HASH-проверка идёт ПЕРЕД TOKEN-проверкой: имена вида `refresh_token_hash`
    # / `pat_hash` / `bot_token_hash` относятся к хэшам токенов (метка
    # `<HASH>`), не к самим токенам. Сервисные копии loging/server не держат
    # эти имена в _HASH_KEYS — там разница приоритета незаметна.
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
    if _OAUTH_CLIENT_SECRET_RE.match(value):
        return "<SECRET>"
    if _BCRYPT_RE.match(value) or _ARGON2_RE.match(value):
        return "<HASH>"
    return None


def _redact_value(value: Any, key_placeholder: str | None) -> Any:
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
    срабатывали upstream-каппы на размер тела. На глубине > `_MAX_DEPTH`
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
