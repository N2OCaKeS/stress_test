"""dispatch_task_with_hit — пробрасывает флаг idempotent_hit наверх.

Caller (`_dispatch_power` и аналоги в `worker_dispatch.py`) кладёт флаг в
audit details, чтобы SIEM отличал «новая task» от «idempotent replay» —
без флага оба сценария возвращают валидный task_id, и метрика «доля
повторов» неотличима.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.core.exceptions import ConflictError
from src.services import worker_client


@pytest.fixture
def stub_dispatch_internals(monkeypatch):
    """Минимальный набор моков, чтобы dispatch_task пошёл через INSERT путь."""
    inserted: list[dict] = []
    outbox_inserted: list[dict] = []

    async def fake_insert(**kwargs):
        inserted.append(kwargs)

    async def fake_delete(_id):
        pass

    async def fake_outbox_insert(_db, *, task_id, task_kind, payload):
        outbox_inserted.append(
            {"task_id": task_id, "task_kind": task_kind, "payload": payload}
        )

    monkeypatch.setattr(worker_client, "_insert_task_row", fake_insert)
    monkeypatch.setattr(worker_client, "_delete_task_row", fake_delete)
    monkeypatch.setattr(
        worker_client.dispatch_outbox_repo, "insert", fake_outbox_insert
    )

    class _Bag:
        pass

    bag = _Bag()
    bag.inserted = inserted
    bag.outbox_inserted = outbox_inserted
    return bag


class TestReturnHit:
    async def test_new_task_returns_hit_false(self, stub_dispatch_internals, monkeypatch):
        async def lookup(_key):
            return None
        monkeypatch.setattr(
            worker_client, "_get_task_by_idempotency_key", lookup,
        )
        task_id, hit = await worker_client.dispatch_task_with_hit(
            db=AsyncMock(),
            task_kind="power.on",
            target_server_id="srv_abc",
            payload={"server_id": "srv_abc"},
            created_by="usr_x",
            request_id="req_1",
            idempotency_key="key-1",
        )
        assert task_id.startswith("tsk_")
        assert hit is False
        # Новая task — outbox-row тоже создан
        assert len(stub_dispatch_internals.outbox_inserted) == 1

    async def test_existing_task_returns_hit_true(self, stub_dispatch_internals, monkeypatch):
        async def lookup(_key):
            return "tsk_existing", "power.on", "srv_abc"
        monkeypatch.setattr(
            worker_client, "_get_task_by_idempotency_key", lookup,
        )
        result = await worker_client.dispatch_task_with_hit(
            db=AsyncMock(),
            task_kind="power.on",
            target_server_id="srv_abc",
            payload={"server_id": "srv_abc"},
            created_by="usr_x",
            request_id="req_1",
            idempotency_key="key-existing",
        )
        assert result == ("tsk_existing", True)
        # INSERT не дёргался — идемпотентный путь
        assert stub_dispatch_internals.inserted == []
        # Idempotent-hit: outbox-INSERT тоже не делается (предыдущий dispatch
        # уже его записал — повторная запись плодила бы дубль публикации).
        assert stub_dispatch_internals.outbox_inserted == []

    async def test_reuse_key_for_other_task_kind_raises_conflict(
        self, stub_dispatch_internals, monkeypatch,
    ):
        """Тот же ключ под другой task_kind → 409 IDEMPOTENCY_KEY_REUSE_CONFLICT."""
        async def lookup(_key):
            return "tsk_existing", "power.on", "srv_abc"
        monkeypatch.setattr(
            worker_client, "_get_task_by_idempotency_key", lookup,
        )
        with pytest.raises(ConflictError) as exc_info:
            await worker_client.dispatch_task(
                db=AsyncMock(),
                task_kind="power.off",
                target_server_id="srv_abc",
                payload={"server_id": "srv_abc"},
                created_by="usr_x",
                request_id="req_1",
                idempotency_key="reused-key",
            )
        assert exc_info.value.error_code == "IDEMPOTENCY_KEY_REUSE_CONFLICT"
        # INSERT не вызывался — отбили до записи
        assert stub_dispatch_internals.inserted == []
        assert stub_dispatch_internals.outbox_inserted == []

    async def test_reuse_key_for_other_target_server_raises_conflict(
        self, stub_dispatch_internals, monkeypatch,
    ):
        """Тот же ключ + тот же kind, но другой target_server_id → 409."""
        async def lookup(_key):
            return "tsk_existing", "power.on", "srv_abc"
        monkeypatch.setattr(
            worker_client, "_get_task_by_idempotency_key", lookup,
        )
        with pytest.raises(ConflictError) as exc_info:
            await worker_client.dispatch_task(
                db=AsyncMock(),
                task_kind="power.on",
                target_server_id="srv_xyz",
                payload={"server_id": "srv_xyz"},
                created_by="usr_x",
                request_id="req_1",
                idempotency_key="reused-key",
            )
        assert exc_info.value.error_code == "IDEMPOTENCY_KEY_REUSE_CONFLICT"
        assert stub_dispatch_internals.inserted == []
        assert stub_dispatch_internals.outbox_inserted == []

    async def test_reuse_key_for_same_op_returns_existing(
        self, stub_dispatch_internals, monkeypatch,
    ):
        """Тот же ключ + тот же kind + тот же server → старый task_id, без INSERT."""
        async def lookup(_key):
            return "tsk_same", "power.on", "srv_abc"
        monkeypatch.setattr(
            worker_client, "_get_task_by_idempotency_key", lookup,
        )
        result = await worker_client.dispatch_task(
            db=AsyncMock(),
            task_kind="power.on",
            target_server_id="srv_abc",
            payload={"server_id": "srv_abc"},
            created_by="usr_x",
            request_id="req_1",
            idempotency_key="same-op-key",
        )
        assert result == "tsk_same"
        assert stub_dispatch_internals.inserted == []
        assert stub_dispatch_internals.outbox_inserted == []

    async def test_dispatch_task_returns_plain_string(self, stub_dispatch_internals, monkeypatch):
        """`dispatch_task` без `_with_hit` отдаёт чистый str — без union."""
        async def lookup(_key):
            return None
        monkeypatch.setattr(
            worker_client, "_get_task_by_idempotency_key", lookup,
        )
        result = await worker_client.dispatch_task(
            db=AsyncMock(),
            task_kind="power.on",
            target_server_id="srv_abc",
            payload={"server_id": "srv_abc"},
            created_by="usr_x",
            request_id="req_1",
            idempotency_key="key-bc",
        )
        assert isinstance(result, str)
        assert result.startswith("tsk_")
