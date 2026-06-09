"""Health и readiness probe'ы. K8s ходит сюда с интервалом."""

import asyncio
import logging
from datetime import datetime, timezone

from fastapi import APIRouter
from sqlalchemy import text

from src.core import http_clients
from src.db.session import engine
from src.services import audit_service, metrics, worker_client

logger = logging.getLogger(__name__)

router = APIRouter()

# Бюджет на best-effort пинги зависимостей в `/ready`. k8s readinessProbe
# обычно имеет timeout ~1s; держим короче, чтобы probe-таймаут не отбился
# из-за подвисшего upstream'а — мы всё равно не валим /ready на их сбое.
_DEPENDENCY_PING_TIMEOUT_SECONDS = 0.5


@router.get(
    "/health",
    summary="Liveness probe — сервис жив, отдаёт 200 без проверок зависимостей",
    description=(
        "Не трогает БД и не зовёт внешние сервисы. k8s liveness probe ходит "
        "сюда чтобы понять, что процесс не повис. Под audit/rate-limit middleware "
        "не попадает (исключение в `main._HEALTH_PATHS`)."
    ),
)
async def health() -> dict[str, str]:
    """Сервис жив. Без проверок зависимостей — только timestamp."""
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}


async def _ping_worker_redis() -> str:
    """best-effort PING на pooled aioredis-клиент `store_prepare_creds`.

    Возвращает `ok` / `skipped` / `<exc-class>`. Не бросает.
    """
    client = worker_client._prepare_redis_client
    if client is None:
        return "skipped"
    try:
        await asyncio.wait_for(client.ping(), timeout=_DEPENDENCY_PING_TIMEOUT_SECONDS)
        return "ok"
    except Exception as exc:  # noqa: BLE001
        return type(exc).__name__


async def _ping_audit_liveness() -> str:
    """best-effort: жив ли pooled audit-client до loging_service.

    Не делает реального POST'а в /events — это потянет за собой rate-limit
    каталога. Проверяет наличие client'а в lifespan-pool'е (instantiated на
    startup'е) — если он None, ready всё равно зелёный (audit best-effort'ный
    и не блокирует трафик).
    """
    if audit_service._audit_client is None:
        return "skipped"
    # Read-канал к loging — отдельный pool; если его нет (legacy config) —
    # тоже skipped, не блок.
    if http_clients.loging_read_client is None:
        return "audit_only"
    return "ok"


@router.get(
    "/ready",
    summary="Readiness probe — БД доступна, можно принимать трафик",
    description=(
        "БД обязательна: SELECT 1 не прошёл — 500, k8s выводит pod из трафика. "
        "Дополнительно best-effort пингуются Redis (через pooled worker_client) "
        "и audit/read pool'ы к loging_service — их фейл не валит ready (audit "
        "best-effort'ный, dispatch отбьётся 503 на месте), но статус каждого "
        "уезжает в payload для оператора. k8s readiness probe."
    ),
)
async def ready() -> dict:
    """БД доступна — SELECT 1 проходит. Redis/audit — best-effort, в payload.

    В payload также уезжают process-local counters: глубина outbox'а на момент
    последнего dispatch'а и `worker_dispatch_orphans_total` (cross-DB worker-row
    без outbox-row после compensation-фейла). Это per-process snapshot; при
    нескольких pod'ах суммирование — забота внешнего агрегатора (Prometheus +
    `kubectl get pods -l ...`).
    """
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
    redis_status = await _ping_worker_redis()
    audit_status = await _ping_audit_liveness()

    # Operational counters для оператора (best-effort, не валят ready).
    counters: dict = {
        "secrets_decrypt_failures": 0,
        "lazy_reencrypt_failures": 0,   # TODO: counter ещё не заведён в metrics.py
        "dispatch_outbox_pending_depth": 0,
        "worker_dispatch_orphans_total": 0,
        "bulk_endpoints_avg_ms": None,  # TODO: bulk handler не публикует таймеры
    }
    try:
        counters["secrets_decrypt_failures"] = int(metrics.get_secrets_decrypt_failures_total())
    except Exception:  # noqa: BLE001
        pass
    try:
        counters["dispatch_outbox_pending_depth"] = int(metrics.get_dispatch_outbox_pending_depth())
    except Exception:  # noqa: BLE001
        pass
    try:
        counters["worker_dispatch_orphans_total"] = int(metrics.get_worker_dispatch_orphans_total())
    except Exception:  # noqa: BLE001
        pass

    return {
        "status": "ready",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "db": "ok",
        "worker_redis": redis_status,
        "audit": audit_status,
        # Дублируем top-level старые поля под обратную совместимость существующих
        # probe-парсеров; новые поля переехали в `counters`.
        "dispatch_outbox_pending_depth": counters["dispatch_outbox_pending_depth"],
        "worker_dispatch_orphans_total": counters["worker_dispatch_orphans_total"],
        "counters": counters,
    }
