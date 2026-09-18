"""Доступ к `audit_outbox`: запись staged-событий, выборка для drain'а, cleanup.

Запись идёт из `services/audit_outbox.py` (staging-сторона), чтение и
отметка published — из `services/audit_outbox_publisher.py`. Commit
везде на вызывающем: publisher держит `FOR UPDATE SKIP LOCKED`-lock до
своего commit'а и не хочет, чтобы репозиторий его отпускал.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import AuditOutbox


def build_rows(payloads: list[dict]) -> list[AuditOutbox]:
    """Payload'ы → ORM-строки. Порядок сохраняется (drain читает по created_at, id)."""
    rows = []
    for payload in payloads:
        action = payload.get("action")
        rows.append(
            AuditOutbox(
                action=str(action)[:128] if action else None,
                payload=payload,
            )
        )
    return rows


async def add_payloads(db: AsyncSession, payloads: list[dict]) -> list[AuditOutbox]:
    """Положить payload'ы в сессию. Commit — на вызывающем."""
    rows = build_rows(payloads)
    db.add_all(rows)
    return rows


def select_publishable(limit: int, *, now: datetime, exclude_ids: list[int] | None = None):
    """SELECT неопубликованных строк, у которых backoff уже дотикал.

    `FOR UPDATE SKIP LOCKED` — защита от двойной публикации, когда
    testing_service раскатан больше чем в одну реплику: каждый drain-loop
    берёт непересекающееся подмножество. Lock держится до commit'а
    вызывающего, а `published_at` проставляется в той же транзакции —
    конкурент уже не увидит строку, когда lock отпустится.

    `exclude_ids` — строки, которые текущий проход уже пробовал и не смог
    опубликовать. Без этого одна нерабочая строка съедала бы весь batch:
    она по-прежнему `published_at IS NULL` и снова попадала бы в выборку.

    `order_by (created_at, id)` — хронологический порядок доставки;
    `id` третьим ключом нужен, потому что при коллизии `created_at` (весь
    буфер одного запроса пишется одним INSERT'ом и получает одинаковый
    `now()`) порядок иначе недетерминирован.
    """
    stmt = (
        select(AuditOutbox)
        .where(
            AuditOutbox.published_at.is_(None),
            or_(
                AuditOutbox.next_retry_at.is_(None),
                AuditOutbox.next_retry_at <= now,
            ),
        )
        .order_by(AuditOutbox.created_at.asc(), AuditOutbox.id.asc())
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    if exclude_ids:
        stmt = stmt.where(AuditOutbox.id.notin_(exclude_ids))
    return stmt


async def count_pending(db: AsyncSession) -> int:
    """Сколько строк ещё ждёт доставки (для диагностики и тестов)."""
    result = await db.execute(
        select(func.count()).select_from(AuditOutbox).where(AuditOutbox.published_at.is_(None))
    )
    return int(result.scalar() or 0)


async def delete_published_older_than(db: AsyncSession, *, cutoff: datetime) -> int:
    """Снести доставленные (и DLQ-помеченные) строки старше `cutoff`.

    Unpublished не трогаем никогда — это события, которые всё ещё в
    полёте. DLQ-строки уезжают вместе с happy-path'ом: причина отбраковки
    уже ушла в ERROR-лог, держать их в БД годами смысла нет.

    Commit — на вызывающем.
    """
    result = await db.execute(
        delete(AuditOutbox).where(
            AuditOutbox.published_at.is_not(None),
            AuditOutbox.published_at < cutoff,
        )
    )
    return result.rowcount or 0
