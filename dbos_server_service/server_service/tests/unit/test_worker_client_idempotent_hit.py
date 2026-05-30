"""dispatch_task return_hit=True — пробрасывает флаг idempotent_hit наверх.

Caller (`_dispatch_power` и аналоги в `worker_dispatch.py`) кладёт флаг в
audit details, чтобы SIEM отличал «новая task» от «idempotent replay» —
без флага оба сценария возвращают валидный task_id, и метрика «доля
повторов» неотличима.
"""

from __future__ import annotations

import pytest

from src.services import worker_client


@pytest.fixture
def stub_dispatch_internals(monkeypatch):
    """Минимальный набор моков, чтобы dispatch_task пошёл через INSERT путь."""
    inserted: list[dict] = []

    async def fake_insert(**kwargs):
        inserted.append(kwargs)

    async def fake_delete(_id):
        pass

    async def fake_ensure_broker():
        pass

    class _Stub:
        async def kiq(self, _tid):
            return None

    monkeypatch.setattr(worker_client, "_insert_task_row", fake_insert)
    monkeypatch.setattr(worker_client, "_delete_task_row", fake_delete)
    monkeypatch.setattr(worker_client, "_ensure_broker_started", fake_ensure_broker)
    monkeypatch.setattr(worker_client, "_task_stubs", {"power.on": _Stub()})
    return inserted


class TestReturnHit:
    async def test_new_task_returns_hit_false(self, stub_dispatch_internals, monkeypatch):
        async def lookup(_key):
            return None, False
        monkeypatch.setattr(
            worker_client, "_get_task_id_by_idempotency_key", lookup,
        )
        result = await worker_client.dispatch_task(
            task_kind="power.on",
            target_server_id="srv_abc",
            payload={"server_id": "srv_abc"},
            created_by="usr_x",
            request_id="req_1",
            idempotency_key="key-1",
            return_hit=True,
        )
        assert isinstance(result, tuple)
        task_id, hit = result
        assert task_id.startswith("tsk_")
        assert hit is False

    async def test_existing_task_returns_hit_true(self, stub_dispatch_internals, monkeypatch):
        async def lookup(_key):
            return "tsk_existing", True
        monkeypatch.setattr(
            worker_client, "_get_task_id_by_idempotency_key", lookup,
        )
        result = await worker_client.dispatch_task(
            task_kind="power.on",
            target_server_id="srv_abc",
            payload={"server_id": "srv_abc"},
            created_by="usr_x",
            request_id="req_1",
            idempotency_key="key-existing",
            return_hit=True,
        )
        assert result == ("tsk_existing", True)
        # INSERT не дёргался — идемпотентный путь
        assert stub_dispatch_internals == []

    async def test_return_hit_false_keeps_string_return(self, stub_dispatch_internals, monkeypatch):
        """BC: без return_hit=True возвращается str, как раньше."""
        async def lookup(_key):
            return None, False
        monkeypatch.setattr(
            worker_client, "_get_task_id_by_idempotency_key", lookup,
        )
        result = await worker_client.dispatch_task(
            task_kind="power.on",
            target_server_id="srv_abc",
            payload={"server_id": "srv_abc"},
            created_by="usr_x",
            request_id="req_1",
            idempotency_key="key-bc",
        )
        assert isinstance(result, str)
        assert result.startswith("tsk_")
