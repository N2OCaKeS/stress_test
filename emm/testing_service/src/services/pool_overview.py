"""Обзор пула с нуля (§F плана 2026-09-11) — реальные агрегаты очереди и стендов.

Заменяет прежний синтетический `FleetDashboard` фронтенда. Два независимых
блока:

* **Очередь и исходы** — `queue_item_repo.aggregate_pool_overview`, три
  контекста: `all` (весь отдел), `run` (одна кампания), `standalone`
  (самостоятельные запуски, опционально период). Успешные/упавшие считаются
  по последней попытке логического теста (§B1 контракта), не по каждой
  попытке в цепочке retry.
* **Статусы стендов** — живой batch-запрос к `server_service`
  (`server_client.get_servers_status_batch`) поверх стендов отдела. Приоритет
  состояний закреплён владельцем 12 сентября 2026: восстановление →
  актуальный отрицательный ping → выполняющийся тест → готов; отсутствие
  свежих данных ping — отдельное состояние, не выдуманный "недоступен".
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal

from src.core.config import get_settings
from src.core.exceptions import DomainValidationError, NotFoundError
from src.models import TestRun, TestStand
from src.repositories import queue_item as queue_item_repo
from src.repositories import test_run as test_run_repo
from src.repositories import test_stand as test_stand_repo
from src.services import server_client

PoolContext = Literal["all", "run", "standalone"]

# Стенд занят системной операцией восстановления/подготовки, не тестом.
# `acs` — снимок/откат ACS (и последующий авто-`server.prepare`, он держит ту
# же стадию до финального callback'а). `updating` — блокировка astra_update.
_RECOVERING_BUSY_STATES = frozenset({"acs", "updating"})

StandStatus = Literal["recovering", "unreachable", "testing", "ready", "no_data"]


@dataclass(frozen=True)
class StandOverview:
    """Один стенд пула с посчитанным статусом (приоритет — см. module docstring)."""

    stand_id: str
    server_id: str
    status: StandStatus
    busy_state: str | None
    busy_service_name: str | None
    ping_reachable: bool | None
    ping_checked_at: datetime | None


@dataclass(frozen=True)
class PoolOverview:
    context: PoolContext
    test_run_id: str | None
    test_run: TestRun | None
    remaining: int
    running: int
    succeeded: int
    failed: int
    stands: list[StandOverview]
    stand_status_counts: dict[StandStatus, int]
    generated_at: datetime


def _classify_stand(
    *,
    server_found: bool,
    busy_state: str | None,
    ping_reachable: bool | None,
    ping_checked_at: datetime | None,
    now: datetime,
    stale_after: timedelta,
) -> StandStatus:
    if not server_found:
        return "no_data"
    if busy_state in _RECOVERING_BUSY_STATES:
        return "recovering"
    stale = ping_checked_at is None or (now - ping_checked_at) > stale_after
    if not stale and ping_reachable is False:
        return "unreachable"
    if busy_state == "testing":
        return "testing"
    if stale:
        return "no_data"
    return "ready"


async def _stand_overviews(db, department_id: str) -> list[StandOverview]:
    stands = await test_stand_repo.list_all(db, limit=1000, department_id=department_id, is_active=True)
    server_ids = [stand.server_id for stand in stands]
    statuses = await server_client.get_servers_status_batch(server_ids) if server_ids else {}
    settings = get_settings()
    stale_after = timedelta(seconds=settings.pool_overview_ping_stale_seconds)
    now = datetime.now(timezone.utc)

    result = []
    for stand in stands:
        info = statuses.get(stand.server_id)
        ping_checked_at = _parse_datetime(info.get("ping_checked_at")) if info else None
        status = _classify_stand(
            server_found=info is not None,
            busy_state=info.get("busy_state") if info else None,
            ping_reachable=info.get("ping_reachable") if info else None,
            ping_checked_at=ping_checked_at,
            now=now, stale_after=stale_after,
        )
        result.append(StandOverview(
            stand_id=stand.id,
            server_id=stand.server_id,
            status=status,
            busy_state=info.get("busy_state") if info else None,
            busy_service_name=info.get("busy_service_name") if info else None,
            ping_reachable=info.get("ping_reachable") if info else None,
            ping_checked_at=ping_checked_at,
        ))
    return result


def _parse_datetime(value) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


async def get_overview(
    db,
    *,
    department_id: str,
    context: PoolContext,
    test_run_id: str | None,
    created_from: datetime | None = None,
    created_until: datetime | None = None,
) -> PoolOverview:
    """Собрать обзор пула под выбранный контекст. Пустой пул/очередь — нули, не 500/null."""
    if context == "run" and not test_run_id:
        raise DomainValidationError(
            error_code="TEST_RUN_ID_REQUIRED",
            message="context=run требует test_run_id",
        )
    if context != "run" and test_run_id:
        raise DomainValidationError(
            error_code="TEST_RUN_ID_NOT_ALLOWED",
            message="test_run_id допустим только при context=run",
        )

    test_run: TestRun | None = None
    if context == "run":
        test_run = await test_run_repo.get_by_id(db, test_run_id)  # type: ignore[arg-type]
        if test_run is None or test_run.department_id != department_id:
            raise NotFoundError(error_code="TEST_RUN_NOT_FOUND", message="Test run not found")

    kind = "campaign" if context == "run" else "standalone" if context == "standalone" else "all"
    counts = await queue_item_repo.aggregate_pool_overview(
        db, department_id,
        kind=kind, test_run_id=test_run_id,
        created_from=created_from, created_until=created_until,
    )

    stands = await _stand_overviews(db, department_id)
    stand_status_counts: dict[StandStatus, int] = {
        "recovering": 0, "unreachable": 0, "testing": 0, "ready": 0, "no_data": 0,
    }
    for stand in stands:
        stand_status_counts[stand.status] += 1

    return PoolOverview(
        context=context,
        test_run_id=test_run_id,
        test_run=test_run,
        remaining=counts["remaining"],
        running=counts["running"],
        succeeded=counts["succeeded"],
        failed=counts["failed"],
        stands=stands,
        stand_status_counts=stand_status_counts,
        generated_at=datetime.now(timezone.utc),
    )
