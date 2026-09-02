"""Валидаторы идентификаторов, которые подставляются в Redis-ключи и URL'ы.

`task_id` приходит из taskiq-broker (Redis RPUSH) — формат генерирует
сам worker (`utils/ids.task_id()` → `tsk_<32 hex>`), но обернуть подстановку
в Redis-ключ без проверки нельзя: при отсутствии AUTH на Redis (staging/dev)
любой клиент брокера может RPUSH с произвольной строкой, и без guard'а
`_IPMI_ROTATE_KEY_PREFIX + task_id` пускает атакующего в чужой keyspace.

`outbox_id` приходит из server_service `/internal/secrets/reencrypt_outbox/...`
и идёт в URL-path; httpx не нормализует `..` сегменты, без guard'а
`f".../{outbox_id}/done"` уязвим к path-traversal при компрометации
server_service.
"""

from __future__ import annotations

import re

# Безопасный alphabet идентификаторов: alnum + `_` + `-` длиной до 64.
# Канонические форматы — `tsk_<32 hex>` (`utils/ids.task_id`) для task_id и
# `rox_<32 hex>` (`server_service.secrets_migration_service.seed_outbox`) для
# outbox_id, оба укладываются в этот alphabet. Более широкое окно нужно для
# тестовых id'ов (`tsk_stash_roundtrip_test`, `rox_x`) и на случай изменения
# id-фабрики, при этом блокирует `:` / `/` / `.` / `..` / spaces — всё, что
# позволило бы прыгнуть из URL-сегмента или Redis-namespace.
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")

# Backward-compat алиасы — тесты могут импортировать конкретное имя; обоим
# соответствует один и тот же regex.
_TASK_ID_RE = _SAFE_ID_RE
_OUTBOX_ID_RE = _SAFE_ID_RE


def validate_task_id(task_id: str) -> str:
    """Проверить формат `task_id` перед подстановкой в Redis-ключ.

    Возвращает `task_id` как есть при успехе; иначе ValueError.
    Применять на каждом call-site'е, где `task_id` идёт в `client.get/set/
    delete(<prefix> + task_id)` — даже если он только что пришёл из
    taskiq message context (broker не валидирует формат).
    """
    if not isinstance(task_id, str) or not _SAFE_ID_RE.fullmatch(task_id):
        raise ValueError("invalid task_id")
    return task_id


def validate_outbox_id(outbox_id: str) -> str:
    """Проверить формат `outbox_id` перед подстановкой в URL.

    `outbox_id` — String(64) в server_service.secrets_reencrypt_outbox
    (`rox_<32 hex>`). Без guard'а path-traversal: `outbox_id="../admin"`
    приводил бы запрос к другому endpoint'у. httpx не нормализует
    `..`-сегменты, поэтому проверка должна быть здесь.
    """
    if not isinstance(outbox_id, str) or not _SAFE_ID_RE.fullmatch(outbox_id):
        raise ValueError("invalid outbox_id")
    return outbox_id
