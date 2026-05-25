"""Тесты periodic cleanup-task'ов для bounded growth БД.

Покрывает:

* **tasks.cleanup_completed_old** — дропает SUCCEEDED/FAILED старше N дней;
  не трогает QUEUED/RUNNING, не трогает свежие completed.
* **audit_outbox.cleanup_published_old** — дропает published row'ы старше
  N дней (delivered + DLQ); unpublished не трогает.

Все тесты — против реальной PostgreSQL.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from src.core.constants import TaskStatus
from src.db.session import AsyncSessionLocal
from src.models import AuditOutbox, Task


def _new_id(prefix: str = "tsk_") -> str:
    return f"{prefix}{uuid.uuid4().hex[:16]}"


async def _insert_task(
    *,
    status: str,
    completed_at: datetime | None,
    enqueued_at: datetime | None = None,
) -> str:
    tid = _new_id()
    async with AsyncSessionLocal() as session:
        session.add(
            Task(
                id=tid,
                task_kind="power.on",
                target_server_id="srv_test",
                payload={"server_id": "srv_test"},
                status=status,
                attempt=1,
                completed_at=completed_at,
                enqueued_at=enqueued_at or datetime.now(timezone.utc),
            )
        )
        await session.commit()
    return tid


async def _all_task_ids() -> set[str]:
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(select(Task.id))).scalars().all()
    return set(rows)


# ╔══════════════════════════════════════════════════════════════════════════╗
# ║ tasks.cleanup_completed_old                                              ║
# ╚══════════════════════════════════════════════════════════════════════════╝


class TestTasksCleanupCompletedOld:
    """Periodic cleanup terminal task'ов старше TASKS_RETENTION_DAYS."""

    async def test_drops_old_succeeded_keeps_fresh(self):
        from src.main import _settings, tasks_cleanup_completed_old

        now = datetime.now(timezone.utc)
        retention = _settings.tasks_retention_days

        old_completed = now - timedelta(days=retention + 1)
        fresh_completed = now - timedelta(hours=1)

        old_id = await _insert_task(
            status=TaskStatus.SUCCEEDED, completed_at=old_completed
        )
        fresh_id = await _insert_task(
            status=TaskStatus.SUCCEEDED, completed_at=fresh_completed
        )

        if hasattr(tasks_cleanup_completed_old, "original_func"):
            await tasks_cleanup_completed_old.original_func()
        else:
            await tasks_cleanup_completed_old()

        remaining = await _all_task_ids()
        assert old_id not in remaining
        assert fresh_id in remaining

    async def test_drops_old_failed(self):
        from src.main import _settings, tasks_cleanup_completed_old

        now = datetime.now(timezone.utc)
        old_failed_id = await _insert_task(
            status=TaskStatus.FAILED,
            completed_at=now - timedelta(days=_settings.tasks_retention_days + 5),
        )

        if hasattr(tasks_cleanup_completed_old, "original_func"):
            await tasks_cleanup_completed_old.original_func()
        else:
            await tasks_cleanup_completed_old()

        assert old_failed_id not in await _all_task_ids()

    async def test_keeps_queued_and_running(self):
        """QUEUED/RUNNING с completed_at IS NULL — не задеваем даже если
        enqueued_at глубоко в прошлом."""
        from src.main import _settings, tasks_cleanup_completed_old

        now = datetime.now(timezone.utc)
        very_old = now - timedelta(days=_settings.tasks_retention_days + 100)

        queued_id = await _insert_task(
            status=TaskStatus.QUEUED,
            completed_at=None,
            enqueued_at=very_old,
        )
        running_id = await _insert_task(
            status=TaskStatus.RUNNING,
            completed_at=None,
            enqueued_at=very_old,
        )

        if hasattr(tasks_cleanup_completed_old, "original_func"):
            await tasks_cleanup_completed_old.original_func()
        else:
            await tasks_cleanup_completed_old()

        remaining = await _all_task_ids()
        assert queued_id in remaining
        assert running_id in remaining

    async def test_custom_retention_via_settings(self, monkeypatch):
        """Понижаем порог до 1 дня — task 2-дневной давности должен умереть."""
        from src.main import _settings, tasks_cleanup_completed_old

        monkeypatch.setattr(_settings, "tasks_retention_days", 1)

        now = datetime.now(timezone.utc)
        two_days_ago_id = await _insert_task(
            status=TaskStatus.SUCCEEDED,
            completed_at=now - timedelta(days=2),
        )
        recent_id = await _insert_task(
            status=TaskStatus.SUCCEEDED,
            completed_at=now - timedelta(hours=6),
        )

        if hasattr(tasks_cleanup_completed_old, "original_func"):
            await tasks_cleanup_completed_old.original_func()
        else:
            await tasks_cleanup_completed_old()

        remaining = await _all_task_ids()
        assert two_days_ago_id not in remaining
        assert recent_id in remaining

    async def test_noop_on_empty_table(self):
        from src.main import tasks_cleanup_completed_old

        if hasattr(tasks_cleanup_completed_old, "original_func"):
            await tasks_cleanup_completed_old.original_func()
        else:
            await tasks_cleanup_completed_old()

        assert await _all_task_ids() == set()

    async def test_task_registered_on_broker(self):
        from src.main import broker

        assert "tasks.cleanup_completed_old" in broker.get_all_tasks()


# ╔══════════════════════════════════════════════════════════════════════════╗
# ║ audit_outbox.cleanup_published_old                                       ║
# ╚══════════════════════════════════════════════════════════════════════════╝


async def _insert_outbox(
    *,
    published_at: datetime | None,
    last_error: str | None = None,
    created_at: datetime | None = None,
) -> int:
    """Вставка outbox-row. Возвращает id (autoincrement)."""
    async with AsyncSessionLocal() as session:
        row = AuditOutbox(
            task_id=_new_id(),
            payload={
                "action": "server.power_on",
                "status": "success",
                "allowed": True,
                "target_id": "srv_test",
            },
            published_at=published_at,
            last_error=last_error,
        )
        if created_at is not None:
            row.created_at = created_at
        session.add(row)
        await session.commit()
        return row.id


async def _all_outbox_ids() -> set[int]:
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(select(AuditOutbox.id))).scalars().all()
    return set(rows)


class TestAuditOutboxCleanupPublishedOld:
    """Periodic cleanup published outbox-row'ов старше
    AUDIT_OUTBOX_RETENTION_DAYS."""

    async def test_drops_old_delivered(self):
        from src.main import _settings, audit_outbox_cleanup_published_old

        now = datetime.now(timezone.utc)
        retention = _settings.audit_outbox_retention_days

        old_id = await _insert_outbox(
            published_at=now - timedelta(days=retention + 1)
        )
        fresh_id = await _insert_outbox(
            published_at=now - timedelta(hours=1)
        )

        if hasattr(audit_outbox_cleanup_published_old, "original_func"):
            await audit_outbox_cleanup_published_old.original_func()
        else:
            await audit_outbox_cleanup_published_old()

        remaining = await _all_outbox_ids()
        assert old_id not in remaining
        assert fresh_id in remaining

    async def test_drops_old_dlq_rows(self):
        """DLQ-row (published_at + last_error) тоже под cleanup."""
        from src.main import _settings, audit_outbox_cleanup_published_old

        now = datetime.now(timezone.utc)
        dlq_old_id = await _insert_outbox(
            published_at=now - timedelta(days=_settings.audit_outbox_retention_days + 2),
            last_error="permanent_4xx",
        )

        if hasattr(audit_outbox_cleanup_published_old, "original_func"):
            await audit_outbox_cleanup_published_old.original_func()
        else:
            await audit_outbox_cleanup_published_old()

        assert dlq_old_id not in await _all_outbox_ids()

    async def test_keeps_unpublished_inflight(self):
        """Unpublished (publisher всё ещё ретраит) НЕ дропаем даже если
        created_at глубоко в прошлом."""
        from src.main import _settings, audit_outbox_cleanup_published_old

        now = datetime.now(timezone.utc)
        very_old_created = now - timedelta(
            days=_settings.audit_outbox_retention_days + 100
        )
        inflight_id = await _insert_outbox(
            published_at=None,
            created_at=very_old_created,
        )

        if hasattr(audit_outbox_cleanup_published_old, "original_func"):
            await audit_outbox_cleanup_published_old.original_func()
        else:
            await audit_outbox_cleanup_published_old()

        assert inflight_id in await _all_outbox_ids()

    async def test_custom_retention_via_settings(self, monkeypatch):
        from src.main import _settings, audit_outbox_cleanup_published_old

        monkeypatch.setattr(_settings, "audit_outbox_retention_days", 1)

        now = datetime.now(timezone.utc)
        two_days_old = await _insert_outbox(
            published_at=now - timedelta(days=2)
        )
        recent = await _insert_outbox(
            published_at=now - timedelta(hours=6)
        )

        if hasattr(audit_outbox_cleanup_published_old, "original_func"):
            await audit_outbox_cleanup_published_old.original_func()
        else:
            await audit_outbox_cleanup_published_old()

        remaining = await _all_outbox_ids()
        assert two_days_old not in remaining
        assert recent in remaining

    async def test_noop_on_empty_table(self):
        from src.main import audit_outbox_cleanup_published_old

        if hasattr(audit_outbox_cleanup_published_old, "original_func"):
            await audit_outbox_cleanup_published_old.original_func()
        else:
            await audit_outbox_cleanup_published_old()

        assert await _all_outbox_ids() == set()

    async def test_task_registered_on_broker(self):
        from src.main import broker

        assert "audit_outbox.cleanup_published_old" in broker.get_all_tasks()
