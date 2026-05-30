"""Маскировка секретов в `details` — защитный слой на стороне loging_service.

Дублирует логику `auth_service/src/services/redaction.py`. Применяется к
`payload.details` перед сохранением, чтобы случайные секреты от любого
источника не попали в БД.

TODO (см. obsidian/TODO.md P4): вынести в общий пакет — sdk либо
отдельный pip-пакет. Сейчас сервисы независимо депло́ятся, общего PYTHONPATH
нет (`auth_service/src` и `loging_service/src` — изолированы), поэтому
просто `from auth_service.services.redaction import redact` не сработает.
Варианты: (a) общий внутренний пакет `dbos_audit_sdk` (наиболее чистый,
но требует своего pyproject + публикации в internal registry),
(b) git-submodule или symlink в общий каталог корня монорепо,
(c) оставить два экземпляра + sync-тест (как у `_DEFAULT_SEVERITY`).
Выбор откладывается, пока схемы redaction не разойдутся (сейчас держим
руками в синхронности).

Плейсхолдеры: `<PASSWORD>`, `<TOKEN>`, `<SECRET>`, `<HASH>`, `<CREDENTIAL>`.

Списки ключей — exact-set lookup по нижнему регистру. В частности,
`_SECRET_KEYS` включает имена ключей S2S-ингеста (`service_api_key`,
`service_key`, `introspect_key`) и `bearer` — auth_service / server_service
аудитят rotate-операции с этими именами в `details`, без них secret уезжал
бы в БД в plaintext.
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
    "service_api_key", "service_key", "introspect_key", "bearer",
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

# Жёсткий cap на глубину обхода `redact()`. Caller'ы (например
# `record_admin_action → _redact_payload`) могут принять подготовленный
# не-нами dict/list, у которого глубина не ограничена ни схемой, ни
# `EventCreate._details_size` (тот считает байты, не nesting). Без
# верхнего предела рекурсия (а теперь итерация) уходит в линейный рост
# памяти на cyclic-friendly nested структуре; обрезаем явно.
_MAX_DEPTH = 64


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
    """Санитизирует dict/list без рекурсии. Не мутирует вход.

    Обход — итеративный через explicit-стек: на глубоком payload'е
    рекурсивная версия упиралась в `RecursionError` (limit ≈ 1000) ещё
    до того, как срабатывали upstream-каппы на размер тела. Стек хранит
    кортежи `(src, dst, key_or_index)`, где `dst[key]` будет заполнен
    результатом обработки `src`. На глубине `_MAX_DEPTH` подставляем
    `"<TRUNCATED>"` плейсхолдер — экраним bombing на pathological-вложенности.
    """
    if not isinstance(payload, (dict, list)):
        return payload

    # Корневой holder — list-обёртка из одного элемента: после прохода
    # `root[0]` хранит готовый результат. Так дальше всегда работаем
    # через единый `parent[key] = ...` контракт, без спец-кейса для root.
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
