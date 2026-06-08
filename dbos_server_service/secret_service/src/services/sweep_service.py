"""Periodic sweep блокированных кред: hard delete после retention-окна.

Запускается из `main.py::lifespan` как фоновый task'и; интервал и retention
конфигурируются в `settings.sweep_interval_seconds` и
`settings.blocked_retention_days`.

Один прогон — одна транзакция. Если что-то падает в середине, всё
откатывается, sweep попробует снова на следующем тике.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete as sa_delete
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import get_settings
from src.db.session import AsyncSessionLocal
from src.models import Credential
from src.services import audit_service

logger = logging.getLogger(__name__)


def _now_utc() -> datetime:
    """Изолировано в функцию, чтобы тестам было легко патчить время."""
    return datetime.now(timezone.utc)


async def sweep_expired_blocked(db: AsyncSession) -> dict:
    """Один проход sweep'а. Возвращает counter для логов / тестов.

    Гонка с recover: ранее использовался SELECT-then-DELETE, между этапами
    `recover` мог поставить `status=active` и sweep всё равно сносил cred по
    PK. Теперь — атомарный `DELETE WHERE status='blocked' AND blocked_at <
    cutoff RETURNING *` за один SQL-выстрел; параллельный recover либо
    выигрывает (его UPDATE отстреливает sweep'овский row), либо проигрывает
    (sweep удаляет до его flush'а).
    """
    settings = get_settings()
    cutoff = _now_utc() - timedelta(days=settings.blocked_retention_days)
    summary = {"deleted_count": 0, "errors": []}
    try:
        stmt = (
            sa_delete(Credential)
            .where(
                Credential.status == "blocked",
                Credential.blocked_at.is_not(None),
                Credential.blocked_at < cutoff,
            )
            .returning(
                Credential.id,
                Credential.scope,
                Credential.service,
                Credential.name,
            )
        )
        deleted_rows = (await db.execute(stmt)).all()
        for row in deleted_rows:
            summary["deleted_count"] += 1
            audit_service.emit(
                "tokens.delete",
                target_id=row.id,
                target_type="credential",
                details={
                    "scope": row.scope,
                    "service": row.service,
                    "name": row.name,
                    "auto_delete": True,
                    "reason": "blocked_window_expired",
                    "retention_days": settings.blocked_retention_days,
                },
            )
        await db.commit()
    except Exception as exc:  # noqa: BLE001
        await db.rollback()
        logger.exception("sweep_expired_blocked: rolled back: %s", exc)
        summary["errors"].append(f"{type(exc).__name__}: {exc}")
    if summary["deleted_count"]:
        logger.info(
            "sweep: deleted %d blocked credentials (retention=%dd)",
            summary["deleted_count"], settings.blocked_retention_days,
        )
    return summary


async def sweep_loop() -> None:
    """Фоновый task: дёргает `sweep_expired_blocked` каждые N секунд.

    Graceful shutdown: на CancelledError выходим тихо. Любая другая ошибка
    в одном тике логируется и не валит цикл — следующий тик попробует
    снова.
    """
    settings = get_settings()
    interval = settings.sweep_interval_seconds
    logger.info("sweep loop started (interval=%ds, retention=%dd)",
                interval, settings.blocked_retention_days)
    try:
        while True:
            try:
                async with AsyncSessionLocal() as session:
                    await sweep_expired_blocked(session)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning("sweep loop tick failed: %s", exc)
            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        logger.info("sweep loop cancelled, exiting cleanly")
        return
