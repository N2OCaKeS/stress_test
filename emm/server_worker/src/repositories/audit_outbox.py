"""AuditOutbox repository — cleanup-only.

INSERT'ы в `audit_outbox` живут в `repositories/task.py::enqueue_audit`
(historically — outbox-row пишется в той же сессии, что и lifecycle
task'ы). Publisher (`services/audit_outbox_publisher.py`) сам делает
SELECT/UPDATE через AsyncSessionLocal. Здесь только bounded-growth
cleanup, чтобы таблица не пухла линейно по числу task'ов.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import AuditOutbox


async def delete_published_older_than(
    db: AsyncSession,
    *,
    cutoff: datetime,
) -> int:
    """Удалить outbox-row'ы с `published_at < cutoff`.

    Под cleanup попадают одновременно happy-path события (опубликованные
    в loging_service) и DLQ-row'ы (`_send_to_dlq` ставит
    `published_at=now()` + хранит `last_error` с причиной отбраковки).
    После настоящего delivery'я row'а в loging_service хранить её здесь
    смысла нет — событие доехало, audit-trail в loging-БД. Аналогично
    DLQ: причину отбраковки оператор должен поймать по ERROR-логу
    `audit_outbox: row sent to DLQ ...` + по будущей метрике
    `audit_outbox_dead_total`; держать DLQ-row'ы в worker-БД годами
    бесполезно.

    Unpublished (`published_at IS NULL`) намеренно НЕ трогаем — это
    in-flight событие, publisher до сих пор пытается его доставить.

    Возвращает количество удалённых строк. Caller отвечает за commit.
    """
    stmt = delete(AuditOutbox).where(
        AuditOutbox.published_at.is_not(None),
        AuditOutbox.published_at < cutoff,
    )
    result = await db.execute(stmt)
    await db.flush()
    return result.rowcount or 0
