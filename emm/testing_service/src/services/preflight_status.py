"""Состояние ожидания внешних сервисов для левой панели UI.

Воркер перед каждым тестом проверяет внешние сервисы (`testing_worker/src/
services/preflight.py`) и, пока хоть один недоступен, ждёт. Раньше это было
видно только строкой в логе item'а; теперь воркер сообщает состояние сюда
(`POST /internal/queue/{id}/preflight-state`), а UI опрашивает
`GET /preflight/status` и показывает «Ожидание доступности сервисов —
тестирование приостановлено».

Отдел берётся из стенда item'а, а не из тела запроса: воркер не может
записать ожидание в чужой отдел. Хранение — Redis (тот же, что у
`creds_stash`), одна запись на item с TTL в два интервала опроса отдела:
упавший воркер не оставляет вечное «ожидание».
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

import redis.asyncio as aioredis
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import get_settings
from src.core.exceptions import NotFoundError
from src.dependencies.auth import Identity
from src.repositories import queue_item as queue_item_repo
from src.repositories import test_stand as test_stand_repo
from src.services import department_test_settings as settings_svc

logger = logging.getLogger("testing_service.preflight_status")

_KEY_PREFIX = "testing:preflight_wait:"
# Запас сверх двух интервалов: сетевая задержка отчёта воркера.
_TTL_SLACK_SECONDS = 60


def _client() -> aioredis.Redis:
    """Клиент под один вызов. Отдельная функция — точка подмены в тестах."""
    return aioredis.from_url(get_settings().redis_url)


def _key(department_id: str, queue_item_id: str) -> str:
    return f"{_KEY_PREFIX}{department_id}:{queue_item_id}"


async def set_state(db: AsyncSession, queue_item_id: str, *, waiting: bool, unavailable: list[str]) -> None:
    """Записать (`waiting`) или снять (`ok`) ожидание внешних сервисов для item'а."""
    item = await queue_item_repo.get_by_id(db, queue_item_id)
    if item is None:
        raise NotFoundError(error_code="QUEUE_ITEM_NOT_FOUND", message="Queue item not found")
    stand = await test_stand_repo.get_by_id(db, item.stand_id)
    department_id = (stand.department_id if stand is not None else None) or "unknown"
    key = _key(department_id, queue_item_id)

    client = _client()
    try:
        if not waiting:
            await client.delete(key)
            return
        existing = await client.get(key)
        since = json.loads(existing)["since"] if existing else datetime.now(timezone.utc).isoformat()
        row = await settings_svc.get_settings_row(db, department_id)
        preflight = settings_svc.effective_preflight(row.preflight if row else None, department_id)
        ttl = 2 * int(preflight["poll_interval_seconds"]) + _TTL_SLACK_SECONDS
        value = {
            "queue_item_id": queue_item_id,
            "stand_id": item.stand_id,
            "unavailable": sorted(set(unavailable)),
            "since": since,
        }
        await client.set(key, json.dumps(value), ex=ttl)
    finally:
        await client.aclose()


async def get_status(identity: Identity) -> dict:
    """Сводка ожидания по отделу пользователя: `waiting`, если ждёт хоть один item."""
    department_id = identity.department_id
    empty = {"state": "ok", "unavailable": [], "since": None, "waiting_items": 0}
    if not department_id:
        return empty

    client = _client()
    try:
        keys = [key async for key in client.scan_iter(match=f"{_KEY_PREFIX}{department_id}:*")]
        raw = await client.mget(keys) if keys else []
    finally:
        await client.aclose()

    records = []
    for value in raw:
        if not value:
            continue
        try:
            records.append(json.loads(value))
        except ValueError:
            logger.warning("preflight_status: unreadable record, skipped")
    if not records:
        return empty
    return {
        "state": "waiting",
        "unavailable": sorted({name for rec in records for name in rec.get("unavailable", [])}),
        "since": min(rec["since"] for rec in records),
        "waiting_items": len(records),
    }
