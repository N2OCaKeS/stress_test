"""Liveness и readiness probe'ы. K8s ходит сюда с интервалом."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from fastapi import APIRouter
from redis.asyncio import Redis
from sqlalchemy import text

from src.core.config import get_settings
from src.db.session import engine
from src.services import audit_service

logger = logging.getLogger(__name__)

router = APIRouter()

# Бюджет на best-effort пинг Redis в `/ready`. k8s readinessProbe обычно имеет
# timeout ~1s; короче, чтобы probe-таймаут не отбился из-за подвисшего upstream'а —
# на сбое Redis ready всё равно отдаёт 200 + degraded.
_REDIS_PING_TIMEOUT_SECONDS = 0.5


@router.get(
    "/health",
    summary="Liveness probe — сервис жив, отдаёт 200 без проверок зависимостей",
    description=(
        "Не трогает БД и не зовёт внешние сервисы. k8s liveness probe ходит "
        "сюда чтобы понять, что процесс не повис."
    ),
)
async def health() -> dict[str, str]:
    """Сервис жив. Без проверок зависимостей — только timestamp."""
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}


async def _check_db() -> bool:
    """SELECT 1. True — БД доступна. На любой ошибке connect/execute — False.

    Каркасная волна не заводит домен — считать строки в таблицах пока
    не из чего, поэтому `/ready` ограничивается самим фактом коннекта.
    """
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
            return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("ready: db probe failed (%s)", exc)
        return False


async def _ping_redis() -> bool:
    """best-effort PING на taskiq-брокер testing_worker'а.

    Отдельный клиент на каждый вызов (не pooled) — `/ready` дёргается редко
    относительно hot-path'ов; заводить постоянный pooled client под один
    PING в этой волне избыточно.
    """
    settings = get_settings()
    try:
        client = Redis.from_url(settings.redis_url)
        try:
            await asyncio.wait_for(client.ping(), timeout=_REDIS_PING_TIMEOUT_SECONDS)
            return True
        finally:
            await client.aclose()
    except Exception as exc:  # noqa: BLE001
        logger.warning("ready: redis ping failed (%s)", exc)
        return False


def _safe_audit_dropped_total() -> int:
    """Per-process counter дропов аудита на 429. На ошибке — 0 + log."""
    try:
        return int(audit_service.get_dropped_429_total())
    except Exception as exc:  # noqa: BLE001
        logger.warning("ready: audit drop counter unreadable (%s)", exc)
        return 0


@router.get(
    "/ready",
    summary="Readiness probe — БД + брокер доступны",
    description=(
        "БД обязательна для зелёного `status=ok`: SELECT 1 не прошёл — "
        "`status=degraded` (HTTP-код всё равно 200, payload — оператору). "
        "Redis (taskiq-брокер testing_worker'а) best-effort пингуется — фейл "
        "не валит ready, но уезжает в payload."
    ),
)
async def ready() -> dict:
    """БД + redis ping + audit-drop counter. На любой ошибке — degraded + log."""
    db_ok = await _check_db()
    redis_connected = await _ping_redis()
    audit_dropped = _safe_audit_dropped_total()
    overall = "ok" if db_ok else "degraded"

    return {
        "status": overall,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "db": db_ok,
        "redis_connected": redis_connected,
        "audit_dropped_429_total": audit_dropped,
        "counters": {
            "audit_dropped_429": audit_dropped,
        },
    }
