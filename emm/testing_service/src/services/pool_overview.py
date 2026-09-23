"""Обзор пула с нуля (§F плана 2026-09-11, доработка 2026-09-23) — реальные
агрегаты очереди и стендов.

Заменяет прежний синтетический `FleetDashboard` фронтенда. Два независимых
блока:

* **Очередь и исходы** — `queue_item_repo.aggregate_pool_overview`.
  `remaining`/`running` всегда по всему отделу (это живое состояние очереди,
  не история — окно на него не влияет). `succeeded`/`failed`, наоборот,
  зависят от режима, который сервис выбирает сам, а не по выбору фронтенда
  (владелец, 2026-09-23 — раньше на UI был переключатель «Все
  задания»/«Выбранный прогон»/«Одиночное тестирование», из которого было
  неясно, что именно считается):

  - `active_run` — если у отдела есть хоть одна незавершённая кампания
    (`test_run.status` не в `TERMINAL_TEST_RUN_STATUSES`), succeeded/failed
    считаются строго по item'ам этой кампании (нескольких, если их несколько
    одновременно), сколько бы дней она ни шла — окно времени тут не при чём.
  - `rolling_24h` — если активных кампаний нет, succeeded/failed берутся за
    последние 24 часа по всему отделу (кампании и одиночные запуски вместе):
    "статистика за сутки", а не вся история с начала времён.

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
from src.models import TestRun, TestStand
from src.repositories import queue_item as queue_item_repo
from src.repositories import test_run as test_run_repo
from src.repositories import test_stand as test_stand_repo
from src.services import server_client

PoolOverviewMode = Literal["active_run", "rolling_24h"]

# Ширина rolling-окна succeeded/failed, когда у отдела нет активной кампании.
_ROLLING_WINDOW = timedelta(hours=24)

# Стенд занят системной операцией восстановления/подготовки, не тестом.
# `acs` — снимок/откат ACS (и последующий авто-`server.prepare`, он держит ту
# же стадию до финального callback'а). `updating` — блокировка astra_update.
_RECOVERING_BUSY_STATES = frozenset({"acs", "updating"})

# Тест закончился, но статус ещё не подтверждён (`acknowledge_testing_done`) —
# стенд не "ready", попытка занять его снова упрётся в бронь.
StandStatus = Literal["recovering", "unreachable", "testing", "testing_done", "ready", "no_data"]


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
    mode: PoolOverviewMode
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
    if busy_state == "testing_done":
        return "testing_done"
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


async def get_overview(db, *, department_id: str) -> PoolOverview:
    """Собрать обзор пула. Сервис сам решает режим агрегации succeeded/failed
    (см. module docstring) — фронтенд больше не выбирает контекст.

    Пустой пул/очередь — нули, не 500/null.
    """
    active_runs = await test_run_repo.list_active(db, department_id)

    # remaining/running — всегда по всему отделу, это текущее состояние
    # очереди, а не история, которую можно "заоконить".
    queue_counts = await queue_item_repo.aggregate_pool_overview(db, department_id, kind="all")

    if active_runs:
        mode: PoolOverviewMode = "active_run"
        run_ids = [run.id for run in active_runs]
        outcome_counts = await queue_item_repo.aggregate_pool_overview(
            db, department_id, kind="campaign", test_run_ids=run_ids,
        )
        # Заголовок карточки показывает конкретную кампанию только когда она
        # одна и однозначна — при нескольких параллельных обзор остаётся
        # агрегатным, без риска подписать общие числа именем случайной из них.
        test_run = active_runs[0] if len(active_runs) == 1 else None
    else:
        mode = "rolling_24h"
        window_from = datetime.now(timezone.utc) - _ROLLING_WINDOW
        outcome_counts = await queue_item_repo.aggregate_pool_overview(
            db, department_id, kind="all", created_from=window_from,
        )
        test_run = None

    stands = await _stand_overviews(db, department_id)
    stand_status_counts: dict[StandStatus, int] = {
        "recovering": 0, "unreachable": 0, "testing": 0, "testing_done": 0, "ready": 0, "no_data": 0,
    }
    for stand in stands:
        stand_status_counts[stand.status] += 1

    return PoolOverview(
        mode=mode,
        test_run_id=test_run.id if test_run else None,
        test_run=test_run,
        remaining=queue_counts["remaining"],
        running=queue_counts["running"],
        succeeded=outcome_counts["succeeded"],
        failed=outcome_counts["failed"],
        stands=stands,
        stand_status_counts=stand_status_counts,
        generated_at=datetime.now(timezone.utc),
    )
