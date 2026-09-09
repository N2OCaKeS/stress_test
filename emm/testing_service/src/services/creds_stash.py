"""Одноразовый Redis-стэш кред тестового пользователя между callback'ом
prepare-for-test и claim'ом `testing_worker`а (§5.1, §5.5 плана миграции).

Формат — plain JSON, без envelope-шифрования. `server_service` шифрует
аналогичный Redis-stash отдельным `REDIS_STASH_ENCRYPTION_KEY`-конвертом
(там креды живут дольше, до нескольких retry-попыток воркера); здесь очередь
сериализована по стенду, TTL короткий (`creds_stash_ttl_seconds`, по
умолчанию 10 минут), ключ одноразовый и читается ровно один раз — заводить
тот же конверт на этой волне избыточно (см. отчёт волны).

Переиспользуем тот же Redis, что и брокер `testing_worker` (`REDIS_URL`) —
отдельного контейнера под это заводить не стали, ключи различает префикс.
"""

from __future__ import annotations

import json
import logging
import uuid

import redis.asyncio as aioredis

from src.core.config import get_settings

logger = logging.getLogger("testing_service.creds_stash")

_KEY_PREFIX = "testing:queue_creds:"


def new_stash_key(queue_item_id: str) -> str:
    """Новый одноразовый ключ для конкретного элемента очереди."""
    return f"{_KEY_PREFIX}{queue_item_id}:{uuid.uuid4().hex}"


def _client() -> aioredis.Redis:
    """Клиент под один вызов. Отдельная функция — точка подмены в тестах."""
    settings = get_settings()
    return aioredis.from_url(settings.redis_url)


async def store_creds(stash_key: str, creds: dict) -> None:
    """Положить креды под `stash_key` с TTL `creds_stash_ttl_seconds`."""
    settings = get_settings()
    client = _client()
    try:
        await client.set(stash_key, json.dumps(creds), ex=settings.creds_stash_ttl_seconds)
    finally:
        await client.aclose()


async def pop_creds(stash_key: str) -> dict | None:
    """Прочитать и удалить креды одной операцией. None — ключ не найден/истёк.

    Одноразовое чтение — `GETDEL` (Redis >= 6.2, версия здесь `^6.0`, что
    указывает на протокол клиента, не на сервер; предполагается совместимый
    Redis-сервер). Если `GETDEL` недоступен на сервере — fallback на
    GET+DEL, менее атомарный, но для этого сценария (одна очередь, один
    читатель) гонки не создаёт.
    """
    client = _client()
    try:
        try:
            raw = await client.execute_command("GETDEL", stash_key)
        except Exception:  # noqa: BLE001 — старый Redis без GETDEL
            raw = await client.get(stash_key)
            if raw is not None:
                await client.delete(stash_key)
        if raw is None:
            return None
        return json.loads(raw)
    finally:
        await client.aclose()
