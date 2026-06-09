"""Liveness и readiness probe'ы. K8s ходит сюда с интервалом."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from fastapi import APIRouter
from sqlalchemy import text

from src.db.session import engine
from src.services import audit_service, reveal_throttle

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


async def _check_db_and_counts() -> tuple[bool, int | None, int | None]:
    """SELECT 1 + COUNT по credentials. Возвращает `(db_ok, total, blocked)`.

    На любой ошибке connect/execute — `(False, None, None)`. Считаем оба COUNT'а
    в одном connect'е, чтобы не дёргать пул дважды per-probe.
    """
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
            total_row = await conn.execute(text("SELECT COUNT(*) FROM credentials"))
            total = int(total_row.scalar() or 0)
            blocked_row = await conn.execute(
                text("SELECT COUNT(*) FROM credentials WHERE status = 'blocked'")
            )
            blocked = int(blocked_row.scalar() or 0)
            return True, total, blocked
    except Exception as exc:  # noqa: BLE001
        logger.warning("ready: db/counts probe failed (%s)", exc)
        return False, None, None


async def _ping_redis() -> bool:
    """best-effort PING на reveal_throttle redis-клиент. False если URI не задан
    или клиент не отвечает в бюджете. Не бросает."""
    client = reveal_throttle._ensure_redis_client()
    if client is None:
        return False
    try:
        ping = getattr(client, "ping", None)
        if ping is None:
            return False
        await asyncio.wait_for(ping(), timeout=_REDIS_PING_TIMEOUT_SECONDS)
        return True
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
    summary="Readiness probe — БД доступна + counters для оператора",
    description=(
        "БД обязательна для зелёного `status=ok`: SELECT 1 не прошёл — "
        "`status=degraded`, k8s держит pod вне трафика по logic'е probe'а "
        "(но HTTP-код всё равно 200, payload — оператору). Redis best-effort "
        "пингуется (`reveal_throttle` shared client) — фейл не валит ready, но "
        "уезжает в payload. Дополнительно: `secrets_total` / `blocked_total` "
        "(SELECT COUNT по `credentials`) и `audit_dropped_429_total` "
        "(per-process)."
    ),
)
async def ready() -> dict:
    """БД + counters + redis ping. На любой ошибке counter'а — degraded + log."""
    db_ok, secrets_total, blocked_total = await _check_db_and_counts()
    redis_connected = await _ping_redis()
    audit_dropped = _safe_audit_dropped_total()
    overall = "ok" if db_ok else "degraded"

    # Доп. counters для observability. lazy_reencrypt_failures и
    # migration_legacy_remaining пока не заведены как in-process счётчики
    # (re-encrypt живёт в server_service, secret_service только хранит
    # ciphertext). Эмитим 0 + TODO, чтобы payload-форма была стабильной
    # для оператора, пока counter не появится.
    counters: dict = {
        "audit_dropped_429": audit_dropped,
        "lazy_reencrypt_failures": 0,        # TODO: counter ещё не заведён
        "migration_legacy_remaining": 0,     # TODO: counter ещё не заведён
        "secrets_total": secrets_total if secrets_total is not None else 0,
        "blocked_total": blocked_total if blocked_total is not None else 0,
    }

    return {
        "status": overall,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "db": db_ok,
        "redis_connected": redis_connected,
        "secrets_total": counters["secrets_total"],
        "blocked_total": counters["blocked_total"],
        "audit_dropped_429_total": audit_dropped,
        "counters": counters,
    }
