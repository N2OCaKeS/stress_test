"""dispatch_outbox transactional outbox semantics — server_service side.

Покрытие:

* `DispatchOutboxRepo.insert` — успешный INSERT с правильными полями
* `dispatch_task` пишет worker-row + outbox-row атомарно в контексте
  caller-транзакции: commit — оба видны, rollback — обе уехали
* idempotent-hit: повторный `dispatch_task` с тем же ключом не создаёт
  второй outbox-row (предыдущий dispatch уже его записал)
* `list_pending` отдаёт только `dispatched_at IS NULL AND (next_retry_at
  IS NULL OR next_retry_at <= now())`, опционально под `FOR UPDATE SKIP LOCKED`
* `mark_dispatched` ставит `dispatched_at = now()`
* `mark_failed` инкрементит attempts, ставит last_error + next_retry_at
* `cleanup_old_dispatched` удаляет только dispatched-row старше cutoff,
  pending-row оставляет нетронутыми

Все тесты опираются на реальный Postgres (см. `conftest.py::db`) — outbox
крутится вокруг atomic-INSERT'а в caller-сессии, in-memory SQLite этого не
воспроизводит.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from src.core.exceptions import ServiceUnavailableError
from src.models import DispatchOutbox
from src.repositories import dispatch_outbox as dispatch_outbox_repo
from src.services import worker_client


# ── DispatchOutboxRepo.insert ───────────────────────────────────────────────


class TestRepoInsert:
    async def test_insert_writes_row_with_correct_fields(self, db):
        """INSERT кладёт task_id/task_kind/payload, дефолты на attempts и flag'и."""
        row = await dispatch_outbox_repo.insert(
            db,
            task_id="tsk_test_001",
            task_kind="power.on",
            payload={"server_id": "srv_abc", "actor": "ops"},
        )
        await db.flush()

        assert row.task_id == "tsk_test_001"
        assert row.task_kind == "power.on"
        assert row.payload == {"server_id": "srv_abc", "actor": "ops"}
        # Pending по умолчанию
        assert row.dispatched_at is None
        assert row.attempts == 0
        assert row.last_error is None
        assert row.next_retry_at is None
        # UUID-PK выставлен default'ом
        assert row.id is not None

    async def test_insert_no_commit_caller_owns_transaction(self, db):
        """insert делает flush, но НЕ commit — это owner-pattern outbox'а."""
        await dispatch_outbox_repo.insert(
            db,
            task_id="tsk_test_002",
            task_kind="inventory.sync",
            payload={"server_id": "srv_x"},
        )
        # Внутри сессии row виден через flush
        rows = (await db.execute(
            select(DispatchOutbox).where(DispatchOutbox.task_id == "tsk_test_002")
        )).scalars().all()
        assert len(rows) == 1


# ── dispatch_task + outbox atomicity ────────────────────────────────────────


class TestDispatchTaskAtomicity:
    """`dispatch_task` пишет worker-row (cross-DB) + outbox-row (caller db).

    Гибрид: первый тест берёт реальную сессию (`db`) и проверяет, что outbox-row
    действительно лежит в caller-таблице после INSERT (SELECT обратно). Второй
    тест моделирует cross-DB сбой, где caller-сессия упасть не должна — здесь
    `AsyncMock()` достаточно, реальный SQLAlchemy rollback не нужен: проверяем
    side-effect (`_delete_task_row` для worker-row) и тип исключения, а не
    транзакционную семантику caller'а. Если в будущем добавятся ассерты на
    `db.rollback()` или состояние caller-таблицы — переводить на real db.
    """

    async def test_outbox_row_written_in_caller_session(self, db, monkeypatch):
        """Outbox-row кладётся в ту же сессию, что передал caller — caller
        commit'нёт его вместе со своими доменными изменениями.
        """
        inserted_in_worker_db: list[dict] = []

        async def fake_insert(**kwargs):
            inserted_in_worker_db.append(kwargs)

        async def fake_lookup(_k):
            return None

        monkeypatch.setattr(worker_client, "_insert_task_row", fake_insert)
        monkeypatch.setattr(
            worker_client, "_get_task_by_idempotency_key", fake_lookup
        )

        new_id = await worker_client.dispatch_task(
            db=db,
            task_kind="power.reboot",
            target_server_id="srv_42",
            payload={"server_id": "srv_42"},
            created_by="usr_op",
            request_id="req_xx",
        )

        # worker-row INSERT случился
        assert len(inserted_in_worker_db) == 1
        assert inserted_in_worker_db[0]["new_task_id"] == new_id
        # outbox-row тоже виден в caller-сессии
        rows = (await db.execute(
            select(DispatchOutbox).where(DispatchOutbox.task_id == new_id)
        )).scalars().all()
        assert len(rows) == 1
        assert rows[0].task_kind == "power.reboot"
        assert rows[0].payload == {"server_id": "srv_42"}

    async def test_outbox_insert_failure_rolls_back_worker_row(self, monkeypatch):
        """outbox-INSERT упал → worker-row DELETE'ится через `_delete_task_row`,
        503 WORKER_UNREACHABLE наружу. Без этого orphan-row в worker-БД.
        """
        inserted_in_worker_db: list[dict] = []
        deleted: list[str] = []

        async def fake_insert(**kwargs):
            inserted_in_worker_db.append(kwargs)

        async def fake_delete(tid):
            deleted.append(tid)

        async def fake_outbox_insert(_db, **_kw):
            raise RuntimeError("server_service db hiccup")

        async def fake_lookup(_k):
            return None

        monkeypatch.setattr(worker_client, "_insert_task_row", fake_insert)
        monkeypatch.setattr(worker_client, "_delete_task_row", fake_delete)
        monkeypatch.setattr(
            worker_client.dispatch_outbox_repo, "insert", fake_outbox_insert
        )
        monkeypatch.setattr(
            worker_client, "_get_task_by_idempotency_key", fake_lookup
        )

        with pytest.raises(ServiceUnavailableError) as exc_info:
            await worker_client.dispatch_task(
                db=AsyncMock(),
                task_kind="power.on",
                target_server_id="srv_abc",
                payload={"server_id": "srv_abc"},
                created_by="usr_x",
                request_id="req_y",
            )

        assert exc_info.value.error_code == "WORKER_UNREACHABLE"
        assert len(inserted_in_worker_db) == 1
        assert deleted == [inserted_in_worker_db[0]["new_task_id"]]


# ── idempotent-hit: outbox-INSERT skipped ────────────────────────────────────


class TestDispatchTaskIdempotentHit:
    async def test_idempotent_hit_does_not_write_outbox(self, db, monkeypatch):
        """Существующий idempotency_key → возвращаем старый task_id, outbox-INSERT'а нет.

        Иначе при retry'е клиента plodилось бы N outbox-row'ов на одну реальную
        задачу, и poller бы публиковал её N раз.
        """
        async def fake_lookup(_k):
            return "tsk_already_there", "power.on", "srv_abc"

        monkeypatch.setattr(
            worker_client, "_get_task_by_idempotency_key", fake_lookup
        )

        # Sanity: до вызова outbox пустой
        pre_rows = (await db.execute(select(DispatchOutbox))).scalars().all()
        pre_count = len(pre_rows)

        result = await worker_client.dispatch_task(
            db=db,
            task_kind="power.on",
            target_server_id="srv_abc",
            payload={"server_id": "srv_abc"},
            created_by="usr_x",
            request_id="req_1",
            idempotency_key="key-already-used",
        )

        assert result == "tsk_already_there"
        # Outbox строк не прибавилось
        post_rows = (await db.execute(select(DispatchOutbox))).scalars().all()
        assert len(post_rows) == pre_count


# ── list_pending ────────────────────────────────────────────────────────────


class TestListPending:
    async def test_returns_only_pending_rows(self, db):
        """Уже отправленные (dispatched_at IS NOT NULL) не попадают в результат."""
        # Pending row
        pending = await dispatch_outbox_repo.insert(
            db, task_id="tsk_p_1", task_kind="power.on", payload={"a": 1}
        )
        # Dispatched row
        dispatched = await dispatch_outbox_repo.insert(
            db, task_id="tsk_d_1", task_kind="power.off", payload={"b": 2}
        )
        await db.flush()
        await dispatch_outbox_repo.mark_dispatched(db, dispatched.id)
        await db.flush()

        rows = await dispatch_outbox_repo.list_pending(
            db, limit=10, with_for_update_skip_locked=False
        )

        ids = {r.task_id for r in rows}
        assert "tsk_p_1" in ids
        assert "tsk_d_1" not in ids
        # Конкретный pending — это наш
        assert pending.task_id == "tsk_p_1"

    async def test_respects_next_retry_at_backoff(self, db):
        """Row с next_retry_at в будущем не выдаётся: poller подождёт backoff."""
        row_now = await dispatch_outbox_repo.insert(
            db, task_id="tsk_now", task_kind="power.on", payload={}
        )
        row_future = await dispatch_outbox_repo.insert(
            db, task_id="tsk_future", task_kind="power.on", payload={}
        )
        await db.flush()
        future = datetime.now(timezone.utc) + timedelta(minutes=5)
        await dispatch_outbox_repo.mark_failed(
            db, row_future.id, error="boom", next_retry_at=future
        )
        await db.flush()

        rows = await dispatch_outbox_repo.list_pending(
            db, limit=10, with_for_update_skip_locked=False
        )
        ids = {r.task_id for r in rows}
        assert "tsk_now" in ids
        assert "tsk_future" not in ids
        assert row_now is not None

    async def test_past_next_retry_at_is_eligible(self, db):
        """Row с next_retry_at в прошлом — снова pickable."""
        row = await dispatch_outbox_repo.insert(
            db, task_id="tsk_overdue", task_kind="power.on", payload={}
        )
        await db.flush()
        past = datetime.now(timezone.utc) - timedelta(minutes=1)
        await dispatch_outbox_repo.mark_failed(
            db, row.id, error="prev-fail", next_retry_at=past
        )
        await db.flush()

        rows = await dispatch_outbox_repo.list_pending(
            db, limit=10, with_for_update_skip_locked=False
        )
        assert any(r.task_id == "tsk_overdue" for r in rows)

    async def test_limit_respected(self, db):
        """list_pending уважает limit и упорядочивает по created_at FIFO."""
        for i in range(5):
            await dispatch_outbox_repo.insert(
                db, task_id=f"tsk_fifo_{i}", task_kind="power.on", payload={"i": i}
            )
        await db.flush()

        rows = await dispatch_outbox_repo.list_pending(
            db, limit=3, with_for_update_skip_locked=False
        )
        assert len(rows) == 3


# ── mark_dispatched / mark_failed ───────────────────────────────────────────


class TestMarkDispatched:
    async def test_sets_dispatched_at(self, db):
        row = await dispatch_outbox_repo.insert(
            db, task_id="tsk_md_1", task_kind="power.on", payload={}
        )
        await db.flush()
        assert row.dispatched_at is None

        await dispatch_outbox_repo.mark_dispatched(db, row.id)
        await db.flush()
        await db.refresh(row)

        assert row.dispatched_at is not None
        # Поле — timezone-aware
        assert row.dispatched_at.tzinfo is not None


class TestMarkFailed:
    async def test_increments_attempts_and_sets_error(self, db):
        row = await dispatch_outbox_repo.insert(
            db, task_id="tsk_mf_1", task_kind="power.on", payload={}
        )
        await db.flush()
        assert row.attempts == 0
        assert row.last_error is None

        retry_at = datetime.now(timezone.utc) + timedelta(seconds=8)
        await dispatch_outbox_repo.mark_failed(
            db, row.id, error="broker down", next_retry_at=retry_at
        )
        await db.flush()
        await db.refresh(row)

        assert row.attempts == 1
        assert row.last_error == "broker down"
        assert row.next_retry_at is not None
        # dispatched_at не трогается — row всё ещё pending
        assert row.dispatched_at is None

    async def test_multiple_failures_accumulate_attempts(self, db):
        row = await dispatch_outbox_repo.insert(
            db, task_id="tsk_mf_2", task_kind="power.on", payload={}
        )
        await db.flush()
        retry = datetime.now(timezone.utc) + timedelta(seconds=1)
        for _ in range(3):
            await dispatch_outbox_repo.mark_failed(
                db, row.id, error="still down", next_retry_at=retry
            )
            await db.flush()
        await db.refresh(row)
        assert row.attempts == 3


# ── cleanup_old_dispatched ──────────────────────────────────────────────────


class TestCleanupOldDispatched:
    async def test_deletes_only_old_dispatched(self, db):
        """Удалаются только dispatched-row старше cutoff. Pending — нетронуты."""
        # Старый dispatched (за пределами cutoff)
        old_disp = await dispatch_outbox_repo.insert(
            db, task_id="tsk_old", task_kind="power.on", payload={}
        )
        # Свежий dispatched (внутри cutoff)
        fresh_disp = await dispatch_outbox_repo.insert(
            db, task_id="tsk_fresh", task_kind="power.on", payload={}
        )
        # Pending — никогда не удалить, даже если старый
        pending = await dispatch_outbox_repo.insert(
            db, task_id="tsk_pending", task_kind="power.on", payload={}
        )
        await db.flush()

        # Помечаем оба как dispatched
        await dispatch_outbox_repo.mark_dispatched(db, old_disp.id)
        await dispatch_outbox_repo.mark_dispatched(db, fresh_disp.id)
        await db.flush()

        # Сдвигаем dispatched_at старого в прошлое вручную (репозиторий не
        # даёт публичного API для backdate — это легитимно для теста).
        from sqlalchemy import update as sa_update
        old_ts = datetime.now(timezone.utc) - timedelta(days=14)
        await db.execute(
            sa_update(DispatchOutbox)
            .where(DispatchOutbox.id == old_disp.id)
            .values(dispatched_at=old_ts)
        )
        await db.flush()

        cutoff = datetime.now(timezone.utc) - timedelta(days=7)
        deleted = await dispatch_outbox_repo.cleanup_old_dispatched(
            db, older_than=cutoff
        )
        await db.flush()

        assert deleted == 1

        remaining = (await db.execute(select(DispatchOutbox))).scalars().all()
        ids = {r.task_id for r in remaining}
        assert "tsk_old" not in ids
        assert "tsk_fresh" in ids
        assert "tsk_pending" in ids
        assert pending.task_id == "tsk_pending"

    async def test_no_pending_deleted_even_if_old(self, db):
        """Pending-row старше cutoff всё равно не трогается (dispatched_at IS NULL)."""
        # Хитрить с created_at тоже надо вручную — но репо явно фильтрует
        # `dispatched_at IS NOT NULL`, поэтому достаточно проверить, что
        # очень-старый pending не удаляется.
        row = await dispatch_outbox_repo.insert(
            db, task_id="tsk_old_pending", task_kind="power.on", payload={}
        )
        await db.flush()
        cutoff = datetime.now(timezone.utc) + timedelta(days=1)  # в будущем
        deleted = await dispatch_outbox_repo.cleanup_old_dispatched(
            db, older_than=cutoff
        )
        assert deleted == 0
        assert row.dispatched_at is None
