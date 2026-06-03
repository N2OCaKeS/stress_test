"""Unit-тесты: `worker_client.dispatch_task` — каскадный rollback на сбой outbox-INSERT'а.

После переключения на transactional outbox прямой публикации в Redis из
`dispatch_task` больше нет: сначала INSERT в `dev_server_worker.tasks`
(cross-DB), потом INSERT строки в `dispatch_outbox` (server_service-БД,
сессия caller'а), commit делает caller. Между двумя INSERT'ами есть окно,
где worker-row уже коммитнут, а outbox-INSERT падает (например, сервисная
БД на короткое время недоступна). Здесь проверяем, что в этом случае
worker-row откатывается через `_delete_task_row` и наружу поднимается
`ServiceUnavailableError(WORKER_UNREACHABLE)`.

Без отката orphan-task навсегда остался бы в `dev_server_worker.tasks`
в статусе `queued`, без записи в outbox его никто не опубликует.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from src.core.exceptions import ServiceUnavailableError
from src.services import worker_client


@pytest.fixture
def patched_internals(monkeypatch):
    """Перехватывает `_insert_task_row`, `_delete_task_row`, `dispatch_outbox_repo.insert`."""

    class Bag:
        def __init__(self) -> None:
            self.inserted: list[dict] = []
            self.deleted: list[str] = []
            self.outbox_inserted: list[dict] = []
            self.outbox_insert_exc: Exception | None = None

    bag = Bag()

    async def fake_insert(**kwargs: Any) -> None:
        bag.inserted.append(kwargs)

    async def fake_delete(task_id_to_delete: str) -> None:
        bag.deleted.append(task_id_to_delete)

    async def fake_outbox_insert(_db, *, task_id, task_kind, payload):
        bag.outbox_inserted.append(
            {"task_id": task_id, "task_kind": task_kind, "payload": payload}
        )
        if bag.outbox_insert_exc is not None:
            raise bag.outbox_insert_exc

    monkeypatch.setattr(worker_client, "_insert_task_row", fake_insert)
    monkeypatch.setattr(worker_client, "_delete_task_row", fake_delete)
    monkeypatch.setattr(
        worker_client.dispatch_outbox_repo, "insert", fake_outbox_insert
    )
    return bag


# ── Happy path: worker-row + outbox-row INSERTed, no rollback ────────────────


class TestDispatchTaskHappyPath:
    async def test_outbox_success_returns_task_id_no_delete(self, patched_internals):
        """outbox-INSERT прошёл → row не удаляется, dispatch_task возвращает id."""
        result = await worker_client.dispatch_task(
            db=AsyncMock(),
            task_kind="power.on",
            target_server_id="srv_abc",
            payload={"server_id": "srv_abc"},
            created_by="usr_xx",
            request_id="req_1",
        )

        assert result.startswith("tsk_")
        # INSERT в worker-БД случился ровно один раз
        assert len(patched_internals.inserted) == 1
        assert patched_internals.inserted[0]["new_task_id"] == result
        assert patched_internals.inserted[0]["task_kind"] == "power.on"
        # outbox-INSERT тоже ровно один, с теми же task_id/task_kind
        assert len(patched_internals.outbox_inserted) == 1
        assert patched_internals.outbox_inserted[0]["task_id"] == result
        assert patched_internals.outbox_inserted[0]["task_kind"] == "power.on"
        assert patched_internals.outbox_inserted[0]["payload"] == {
            "server_id": "srv_abc"
        }
        # rollback не понадобился
        assert patched_internals.deleted == []


# ── outbox-INSERT raises → worker-row откатывается, 503 WORKER_UNREACHABLE ──


class TestDispatchTaskOutboxFailureRollback:
    """`dispatch_outbox_repo.insert` поднимает исключение.

    После commit'а worker-row (cross-DB INSERT) outbox-INSERT упал — это и
    есть тот самый race-window, который нужно откатить. Ожидание: DELETE
    worker-row + `ServiceUnavailableError(WORKER_UNREACHABLE)` с chained
    причиной.
    """

    async def test_outbox_runtime_error_deletes_row_and_raises_503(
        self, patched_internals
    ):
        patched_internals.outbox_insert_exc = RuntimeError(
            "server_service db connection lost"
        )

        with pytest.raises(ServiceUnavailableError) as exc_info:
            await worker_client.dispatch_task(
                db=AsyncMock(),
                task_kind="power.on",
                target_server_id="srv_abc",
                payload={"server_id": "srv_abc"},
                created_by="usr_xx",
                request_id="req_1",
            )

        assert exc_info.value.error_code == "WORKER_UNREACHABLE"
        assert exc_info.value.http_status == 503
        assert "RuntimeError" in exc_info.value.message
        # worker-row откатан
        assert len(patched_internals.inserted) == 1
        inserted_id = patched_internals.inserted[0]["new_task_id"]
        assert patched_internals.deleted == [inserted_id]
        # __cause__ цепочка через `raise ... from exc`
        assert isinstance(exc_info.value.__cause__, RuntimeError)

    async def test_outbox_connection_error_deletes_row(self, patched_internals):
        """ConnectionError при INSERT'е в outbox → тот же путь WORKER_UNREACHABLE."""
        patched_internals.outbox_insert_exc = ConnectionError("postgres unreachable")

        with pytest.raises(ServiceUnavailableError) as exc_info:
            await worker_client.dispatch_task(
                db=AsyncMock(),
                task_kind="power.on",
                target_server_id="srv_abc",
                payload={"server_id": "srv_abc"},
                created_by="usr_xx",
                request_id="req_1",
            )

        assert exc_info.value.error_code == "WORKER_UNREACHABLE"
        assert "ConnectionError" in exc_info.value.message
        assert len(patched_internals.deleted) == 1


# ── Idempotency не теряется: повторный путь после rollback ──────────────────


class TestDispatchTaskRollbackDoesNotBreakIdempotency:
    """Sanity: после rollback'а клиент может повторить с тем же idempotency_key
    и попасть в нормальный путь (outbox-INSERT на этот раз пройдёт).
    """

    async def test_retry_after_rollback_creates_fresh_row(
        self, patched_internals, monkeypatch
    ):
        async def fake_lookup(_key: str):
            # До retry'я строки нет — DELETE сработал
            return None

        monkeypatch.setattr(
            worker_client, "_get_task_by_idempotency_key", fake_lookup,
        )

        # 1-й вызов: outbox-INSERT падает → DELETE → 503
        patched_internals.outbox_insert_exc = ConnectionError("flaky db")
        with pytest.raises(ServiceUnavailableError):
            await worker_client.dispatch_task(
                db=AsyncMock(),
                task_kind="power.on",
                target_server_id="srv_abc",
                payload={"server_id": "srv_abc"},
                created_by="usr_xx",
                request_id="req_1",
                idempotency_key="client-key-77",
            )

        first_inserted = patched_internals.inserted[0]["new_task_id"]
        assert patched_internals.deleted == [first_inserted]

        # 2-й вызов: outbox-INSERT успешно — row остаётся
        patched_internals.outbox_insert_exc = None
        result = await worker_client.dispatch_task(
            db=AsyncMock(),
            task_kind="power.on",
            target_server_id="srv_abc",
            payload={"server_id": "srv_abc"},
            created_by="usr_xx",
            request_id="req_2",
            idempotency_key="client-key-77",
        )

        assert result != first_inserted
        assert result.startswith("tsk_")
        assert len(patched_internals.inserted) == 2
        assert patched_internals.deleted == [first_inserted]


# ── `_delete_task_row` сам по себе — best-effort, никогда не raise'ит ────────


class TestDeleteTaskRowBestEffort:
    """`_delete_task_row` ВСЕГДА возвращает None — никогда не пробрасывает ошибку.

    Если DELETE упадёт (worker-БД тоже недоступна), клиент всё равно должен
    получить правильный 503 от первичной ошибки публикации, а не быть
    замаскированным вторичной DB-ошибкой.
    """

    async def test_delete_swallows_engine_factory_error(self, monkeypatch):
        def boom_factory():
            raise ServiceUnavailableError(
                error_code="WORKER_DB_NOT_CONFIGURED",
                message="SERVER_WORKER_DATABASE_URL is not set",
            )

        monkeypatch.setattr(worker_client, "_engine_factory", boom_factory)

        result = await worker_client._delete_task_row("tsk_zombie_42")
        assert result is None

    async def test_delete_swallows_generic_runtime_error(self, monkeypatch):
        def boom_factory():
            raise RuntimeError("connection pool exhausted")

        monkeypatch.setattr(worker_client, "_engine_factory", boom_factory)

        result = await worker_client._delete_task_row("tsk_zombie_99")
        assert result is None
