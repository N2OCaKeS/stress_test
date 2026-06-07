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

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import get_settings
from src.db.session import AsyncSessionLocal
from src.models import Credential
from src.repositories import credentials as cred_repo
from src.services import audit_service

logger = logging.getLogger(__name__)


def _now_utc() -> datetime:
    """Изолировано в функцию, чтобы тестам было легко патчить время."""
    return datetime.now(timezone.utc)


async def _list_blocked_older_than(
    db: AsyncSession, cutoff: datetime
) -> list[Credential]:
    """Все cred'ы со status=blocked и blocked_at < cutoff. Без курсора — батча хватает."""
    stmt = select(Credential).where(
        Credential.status == "blocked",
        Credential.blocked_at.is_not(None),
        Credential.blocked_at < cutoff,
    )
    return list((await db.execute(stmt)).scalars())


async def sweep_expired_blocked(db: AsyncSession) -> dict:
    """Один проход sweep'а. Возвращает counter для логов / тестов."""
    settings = get_settings()
    cutoff = _now_utc() - timedelta(days=settings.blocked_retention_days)
    summary = {"deleted_count": 0, "errors": []}
    try:
        expired = await _list_blocked_older_than(db, cutoff)
        for cred in expired:
            cred_id_snap = cred.id
            cred_scope = cred.scope
            cred_service = cred.service
            cred_name = cred.name
            await cred_repo.delete(db, cred)
            summary["deleted_count"] += 1
            audit_service.emit(
                "tokens.delete",
                target_id=cred_id_snap,
                target_type="credential",
                details={
                    "scope": cred_scope,
                    "service": cred_service,
                    "name": cred_name,
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
