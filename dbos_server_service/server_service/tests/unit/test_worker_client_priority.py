"""Unit-тесты: проброс `priority` через dispatch-хелперы `worker_client`.

`_insert_task_row` пишет `priority` в worker-row. `dispatch_task` /
`dispatch_task_with_hit` принимают опциональный `priority` (дефолт
`TASK_PRIORITY_NORMAL`) и форвардят его в INSERT. На idempotent-hit INSERT'а
нет — priority уже зафиксирован при первой постановке.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from src.services import worker_client


@pytest.fixture
def captured_insert(monkeypatch):
    """Перехватывает `_insert_task_row` и `dispatch_outbox_repo.insert`.

    Outbox-INSERT делаем no-op (happy path), чтобы dispatch дошёл до конца и
    мы могли проверить, с каким priority пошёл worker-INSERT.
    """
    inserted: list[dict] = []

    async def fake_insert(**kwargs: Any) -> None:
        inserted.append(kwargs)

    async def fake_outbox_insert(_db, *, task_id, task_kind, payload):
        return None

    monkeypatch.setattr(worker_client, "_insert_task_row", fake_insert)
    monkeypatch.setattr(
        worker_client.dispatch_outbox_repo, "insert", fake_outbox_insert
    )
    return inserted


class TestPriorityForwarding:
    async def test_default_priority_is_normal(self, captured_insert):
        await worker_client.dispatch_task(
            db=AsyncMock(),
            task_kind="inventory.sync",
            target_server_id="srv_a",
            payload={},
            created_by="usr_x",
            request_id="req_1",
        )
        assert len(captured_insert) == 1
        assert captured_insert[0]["priority"] == worker_client.TASK_PRIORITY_NORMAL
        assert captured_insert[0]["priority"] == 0

    async def test_high_priority_forwarded(self, captured_insert):
        await worker_client.dispatch_task(
            db=AsyncMock(),
            task_kind="power.reboot",
            target_server_id="srv_a",
            payload={},
            created_by="usr_x",
            request_id="req_1",
            priority=worker_client.TASK_PRIORITY_HIGH,
        )
        assert captured_insert[0]["priority"] == 100

    async def test_with_hit_forwards_priority(self, captured_insert):
        task_id, hit = await worker_client.dispatch_task_with_hit(
            db=AsyncMock(),
            task_kind="power.on",
            target_server_id="srv_a",
            payload={},
            created_by="usr_x",
            request_id="req_1",
            priority=worker_client.TASK_PRIORITY_HIGH,
        )
        assert hit is False
        assert task_id.startswith("tsk_")
        assert captured_insert[0]["priority"] == 100

    async def test_idempotent_hit_skips_insert(self, captured_insert, monkeypatch):
        """idempotent-hit → INSERT'а нет, priority нового вызова игнорируется."""
        async def fake_lookup(_key: str):
            return ("tsk_existing", "power.on", "srv_a")

        monkeypatch.setattr(
            worker_client, "_get_task_by_idempotency_key", fake_lookup,
        )

        task_id, hit = await worker_client.dispatch_task_with_hit(
            db=AsyncMock(),
            task_kind="power.on",
            target_server_id="srv_a",
            payload={},
            created_by="usr_x",
            request_id="req_1",
            idempotency_key="dup-key",
            priority=worker_client.TASK_PRIORITY_HIGH,
        )
        assert task_id == "tsk_existing"
        assert hit is True
        assert captured_insert == []
