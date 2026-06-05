"""DispatchOutbox-репозиторий — INSERT/poll/mark API для transactional outbox.

Outbox закрывает race-окно между commit'ом transaction'а, меняющего worker-БД
(`dev_server_worker.tasks`), и публикацией задачи в Redis. Сервис в той же
server_service-транзакции пишет outbox-row с готовым payload'ом — отдельный
poller в server_worker (Phase C) выбирает `dispatched_at IS NULL`, шлёт в
broker и проставляет `dispatched_at = now()`.

Все методы возвращаются БЕЗ commit'а — owner транзакции caller. Это даёт
caller'у атомарно зафиксировать INSERT outbox-row вместе с любыми другими
доменными изменениями в той же server_service-сессии.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import DispatchOutbox


async def insert(
    db: AsyncSession,
    *,
    task_id: str,
    task_kind: str,
    payload: dict[str, Any],
) -> DispatchOutbox:
    """INSERT новой outbox-row в статусе pending (`dispatched_at IS NULL`).

    commit — на caller'е: outbox-row должен лечь в ту же транзакцию, что и
    остальные доменные изменения caller'а (например server.status update),
    иначе теряется смысл outbox'а как atomic write-ahead-log.
    """
    row = DispatchOutbox(
        task_id=task_id,
        task_kind=task_kind,
        payload=payload,
    )
    db.add(row)
    await db.flush()
    return row


async def list_pending(
    db: AsyncSession,
    *,
    limit: int,
    with_for_update_skip_locked: bool = True,
) -> list[DispatchOutbox]:
    """Прочитать pending outbox-rows для poller'а.

    Тянем `dispatched_at IS NULL`, упорядочивая по `created_at` (FIFO).
    Backoff-логика отдельно: `next_retry_at > now()` отсекаем здесь, чтобы
    poller не дёргал свежие падения в каждом тике.

    `FOR UPDATE SKIP LOCKED` нужен на случай нескольких poller-реплик —
    каждая забирает свою порцию без блокировки на чужих row'ах. Параметр
    отключаемый для unit-тестов / single-worker инсталляций, где SKIP LOCKED
    лишний.
    """
    stmt = (
        select(DispatchOutbox)
        .where(DispatchOutbox.dispatched_at.is_(None))
        .where(
            (DispatchOutbox.next_retry_at.is_(None))
            | (DispatchOutbox.next_retry_at <= func.now())
        )
        .order_by(DispatchOutbox.created_at)
        .limit(limit)
    )
    if with_for_update_skip_locked:
        stmt = stmt.with_for_update(skip_locked=True)
    return list((await db.execute(stmt)).scalars())


async def mark_dispatched(db: AsyncSession, outbox_id) -> None:
    """Пометить row как успешно опубликованный в брокер.

    `dispatched_at = now()` — финальное состояние. После этого poller строку
    не подбирает, retention-cron удалит её через `cleanup_old_dispatched`.
    commit — на caller'е.
    """
    await db.execute(
        update(DispatchOutbox)
        .where(DispatchOutbox.id == outbox_id)
        .values(dispatched_at=func.now())
    )


async def mark_failed(
    db: AsyncSession,
    outbox_id,
    *,
    error: str,
    next_retry_at: datetime,
) -> None:
    """Зарегистрировать неудачную попытку публикации.

    `attempts += 1` через SQL-выражение (без read-modify-write), плюс
    `last_error` и `next_retry_at` под backoff. Pending-флаг (`dispatched_at
    IS NULL`) не трогаем — следующий тик после `next_retry_at` row снова
    подберёт. commit — на caller'е.
    """
    await db.execute(
        update(DispatchOutbox)
        .where(DispatchOutbox.id == outbox_id)
        .values(
            attempts=DispatchOutbox.attempts + 1,
            last_error=error,
            next_retry_at=next_retry_at,
        )
    )


async def pending_count(db: AsyncSession) -> int:
    """Сколько pending outbox-row'ов сейчас в очереди (`dispatched_at IS NULL`).

    Снимок depth'а для ops-метрики `dispatch_outbox_pending_depth`. Зовётся
    периодически из cleanup-cron'а / health-check'а — оператор по росту
    счётчика видит, что poller отстал или умер. commit не делаем — read-only.
    """
    stmt = (
        select(func.count(DispatchOutbox.id))
        .where(DispatchOutbox.dispatched_at.is_(None))
    )
    result = await db.execute(stmt)
    return int(result.scalar_one() or 0)


async def cleanup_old_dispatched(
    db: AsyncSession,
    *,
    older_than: datetime,
) -> int:
    """DELETE отправленных row'ов старше порога. Возвращает rowcount.

    Зовётся периодическим cleanup-cron'ом (по аналогии с
    `audit_outbox.cleanup_published_old` в server_worker). Pending-строки
    не трогаем — критерий `dispatched_at < older_than` отсекает
    `dispatched_at IS NULL`. commit — на caller'е.
    """
    result = await db.execute(
        delete(DispatchOutbox)
        .where(DispatchOutbox.dispatched_at.is_not(None))
        .where(DispatchOutbox.dispatched_at < older_than)
    )
    return result.rowcount or 0
