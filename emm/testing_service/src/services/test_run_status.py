"""Пересчёт агрегатного статуса кампании (§2.4, §6.1 плана миграции).

Отдельный лист-модуль без зависимости на `services/queue.py` или
`services/test_run.py` — им обоим нужно вызывать пересчёт (queue.py — на
каждом переходе дочернего item'а в терминал, test_run.py — сразу после
создания кампании), а сами они друг друга не импортируют (`test_run.py`
вызывает `queue.enqueue()`), так что общая логика живёт здесь, чтобы не
заводить цикл импортов.

Статус материализован на `test_runs.status`, не вычисляется на лету при
каждом чтении — иначе `GET /test-runs` с фильтром по `status` требовал бы по
запросу к дочерним `queue_items` на каждую кампанию в странице.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.constants import (
    ACTIVE_QUEUE_STATES,
    FAILURE_QUEUE_STATES,
    QueueItemState,
    TestRunStatus,
)
from src.models import QueueItem, TestRunEntry
from src.repositories import queue_item as queue_item_repo
from src.repositories import test_run as repo
from src.repositories import test_run_entry as entry_repo
from src.services import audit_service


def latest_attempts(items: Sequence[QueueItem]) -> list[QueueItem]:
    """Предыдущие попытки сохраняются в истории, но не считаются текущим исходом."""
    by_id = {item.id: item for item in items}
    superseded = set()
    for item in items:
        parent = by_id.get(item.retry_of_id)
        if parent is not None and (parent.test_run_id, parent.test_id, parent.stand_id, parent.debug_mode) == (item.test_run_id, item.test_id, item.stand_id, item.debug_mode):
            superseded.add(parent.id)
    return [item for item in items if item.id not in superseded]


def result_states(items: Sequence[QueueItem], entries: Sequence[TestRunEntry]) -> list[str]:
    latest = latest_attempts(items)
    attempted_entries = {item.test_run_entry_id for item in items}
    return [item.state for item in latest] + [
        QueueItemState.FAILED if entry.enqueue_error_code else QueueItemState.QUEUED
        for entry in entries if entry.id not in attempted_entries
    ]


def compute_status(states: list[str]) -> str:
    """Чистая функция агрегации — состояния дочерних item'ов → статус кампании.

    Пустой список (ни один стенд пула не дал ни одного item'а — все стенды
    оказались без закреплённых тестов) — `queued`, кампания заведена, но
    работать ей не над чем. Любой нетерминальный item — `running`.

    Из терминальных исходов к провалу тянет `FAILED` и `TIMED_OUT`
    (`FAILURE_QUEUE_STATES`) — таймаут SSH-команды такой же провал теста, как
    и generic `failed`, просто с другой причиной. Чистая смесь этих двух
    исходов даёт `failed`, что угодно из них вперемешку с успехом/пропуском —
    `partially_failed`. Всё остальное (`SUCCEEDED`, `SUCCEEDED` + `SKIPPED`,
    да хоть сплошной `SKIPPED`) — `succeeded`: пропуск это решение оператора,
    а не провал теста. Отдельного агрегатного статуса под пропуск не заводим
    — сколько item'ов пропущено, видно из `progress.skipped` в карточке
    прогона.
    """
    if not states:
        return TestRunStatus.QUEUED
    if any(state in ACTIVE_QUEUE_STATES for state in states):
        return TestRunStatus.RUNNING
    outcomes = set(states)
    if not outcomes & FAILURE_QUEUE_STATES:
        return TestRunStatus.SUCCEEDED
    if outcomes <= FAILURE_QUEUE_STATES:
        return TestRunStatus.FAILED
    return TestRunStatus.PARTIALLY_FAILED


async def recompute(db: AsyncSession, test_run_id: str, *, emit_audit: bool = True) -> str | None:
    """Пересчитать и, если изменилось, сохранить `status` кампании.

    Не коммитит сама — вызывающий (`queue.py`/`services/test_run.py`) уже
    находится внутри своей транзакции постановки/завершения item'а и делает
    commit сам. Возвращает новый статус, либо `None`, если кампания уже не
    существует (не должно случаться при `ON DELETE SET NULL`, но `queue.py`
    не обязан на это полагаться).

    `emit_audit=False` — используется первичной материализацией статуса сразу
    после создания кампании (`services/test_run.py::create_test_run`), где
    итоговый статус и так попадает в details события `test_run.create` —
    отдельная строка `test_run.status_changed` для этого перехода была бы
    просто дублем.
    """
    run = await repo.get_by_id_for_update(db, test_run_id)
    if run is None:
        return None
    items = await queue_item_repo.list_by_test_run_id(db, test_run_id)
    entries = await entry_repo.list_for_run(db, test_run_id)
    new_status = compute_status(result_states(items, entries))
    if run.status != new_status:
        old_status = run.status
        run.status = new_status
        await db.flush()
        if emit_audit:
            audit_service.emit(
                "test_run.status_changed",
                target_id=run.id, target_type="test_run",
                status="success", allowed=True,
                details={"old_status": old_status, "new_status": new_status},
            )
    return new_status
