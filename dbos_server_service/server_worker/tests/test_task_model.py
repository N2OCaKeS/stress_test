"""Тесты Task ORM-модели и схемы БД.

* defaults (status=queued, attempt=0, max_attempts=3, payload={});
* uniqueness `idempotency_key` через IntegrityError;
* NULL idempotency_key — НЕ нарушает UNIQUE (PostgreSQL спецификация);
* индексы созданы миграцией;
* `enqueued_at` server_default = now().
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from src.core.constants import TaskStatus
from src.db.session import AsyncSessionLocal, engine
from src.models import Task


def _new_id(prefix: str = "tsk_") -> str:
    return f"{prefix}{uuid.uuid4().hex[:16]}"


# ── Defaults ─────────────────────────────────────────────────────────────────

class TestDefaults:
    async def test_inserted_with_defaults(self):
        tid = _new_id()
        async with AsyncSessionLocal() as session:
            t = Task(id=tid, task_kind="power.on")
            session.add(t)
            await session.flush()
            await session.commit()

        async with AsyncSessionLocal() as session:
            t = (await session.execute(select(Task).where(Task.id == tid))).scalar_one()
            assert t.status == TaskStatus.QUEUED
            assert t.attempt == 0
            assert t.max_attempts == 3
            assert t.payload == {}
            assert t.enqueued_at is not None
            assert t.started_at is None
            assert t.completed_at is None
            assert t.result is None
            assert t.last_error is None

    async def test_explicit_payload_stored_as_jsonb(self):
        tid = _new_id()
        async with AsyncSessionLocal() as session:
            session.add(Task(
                id=tid, task_kind="x.y",
                payload={"server_id": "srv_1", "nested": {"k": 1}},
            ))
            await session.commit()

        async with AsyncSessionLocal() as session:
            t = (await session.execute(select(Task).where(Task.id == tid))).scalar_one()
            assert t.payload["server_id"] == "srv_1"
            assert t.payload["nested"]["k"] == 1


# ── Idempotency UNIQUE ───────────────────────────────────────────────────────

class TestIdempotencyUnique:
    async def test_duplicate_key_raises_integrity_error(self):
        key = f"idem_{uuid.uuid4().hex[:12]}"

        async with AsyncSessionLocal() as session:
            session.add(Task(id=_new_id(), task_kind="x.y", idempotency_key=key))
            await session.commit()

        async with AsyncSessionLocal() as session:
            session.add(Task(id=_new_id(), task_kind="x.y", idempotency_key=key))
            with pytest.raises(IntegrityError):
                await session.commit()

    async def test_null_idempotency_does_not_trigger_unique(self):
        """В PostgreSQL UNIQUE допускает множественные NULL — фиксируем."""
        async with AsyncSessionLocal() as session:
            session.add(Task(id=_new_id(), task_kind="x.y", idempotency_key=None))
            session.add(Task(id=_new_id(), task_kind="x.y", idempotency_key=None))
            await session.commit()  # без IntegrityError


# ── Schema indexes ──────────────────────────────────────────────────────────

class TestIndexes:
    async def test_required_indexes_present(self):
        async with engine.begin() as conn:
            rows = await conn.execute(text(
                "SELECT indexname FROM pg_indexes WHERE tablename='tasks'"
            ))
            names = {r[0] for r in rows}

        # PK + named indexes из __table_args__
        assert "ix_tasks_kind_status" in names
        assert "ix_tasks_status_enqueued" in names
        # SQLAlchemy с index=True на колонке генерит ix_<table>_<col>
        assert "ix_tasks_task_kind" in names
        assert "ix_tasks_target_server_id" in names

    async def test_idempotency_unique_constraint_present(self):
        async with engine.begin() as conn:
            rows = await conn.execute(text(
                "SELECT conname FROM pg_constraint "
                "WHERE conrelid='tasks'::regclass AND contype='u'"
            ))
            names = {r[0] for r in rows}
        assert "uq_task_idempotency_key" in names


# ── Column types / NOT NULL ──────────────────────────────────────────────────

class TestSchema:
    async def test_required_columns_not_null(self):
        async with engine.begin() as conn:
            result = await conn.execute(text(
                "SELECT column_name, is_nullable FROM information_schema.columns "
                "WHERE table_name='tasks'"
            ))
            cols = {r[0]: r[1] for r in result.all()}

        for required in ("id", "task_kind", "status", "attempt", "max_attempts",
                         "enqueued_at", "payload"):
            assert cols[required] == "NO", f"{required} must be NOT NULL"
        for optional in ("target_server_id", "target_resource_id", "last_error",
                         "result", "started_at", "completed_at", "created_by",
                         "request_id", "idempotency_key"):
            assert cols[optional] == "YES", f"{optional} must allow NULL"

    async def test_payload_default_empty_json(self):
        """server_default='{}' — INSERT без payload через raw SQL даёт {}."""
        tid = _new_id()
        async with engine.begin() as conn:
            await conn.execute(text(
                "INSERT INTO tasks (id, task_kind, status, attempt, max_attempts) "
                "VALUES (:id, :k, 'queued', 0, 3)"
            ), {"id": tid, "k": "x.y"})

        async with AsyncSessionLocal() as session:
            t = (await session.execute(select(Task).where(Task.id == tid))).scalar_one()
            assert t.payload == {}
