"""Авто-inventory + power подготовленных серверов.

Покрывает:
* #2 — после `record_server_prepared` (callback prepare) автоматически
  диспатчатся `inventory.sync` + `power.status` для этого сервера;
* #3 — плановый прогон `POST /internal/servers/auto-inventory-sweep` идёт по
  всем managed-серверам, ставит по паре задач на каждый, пропускает
  неподготовленные/decommissioned, режет хвост по cap'у, требует worker_bot-грант.
"""

from __future__ import annotations

import pytest

from src.core.config import get_settings
from src.core.constants import ServerStatus
from tests._helpers import assert_error, auth_hdr as _hdr

BASE_INT = "/api/server/v1/internal"


@pytest.fixture
def captured_dispatch(monkeypatch):
    """Перехват `dispatch_task_with_hit` в auto_inventory-сервисе.

    Cross-DB INSERT в тестах не делаем — фиксируем только (task_kind,
    target_server_id), чего хватает для проверки фан-аута.
    """
    calls: list[dict] = []

    async def fake_dispatch_with_hit(
        *, db, task_kind, target_server_id, payload, created_by,
        request_id, target_resource_id=None, idempotency_key=None, priority=0,
    ):
        calls.append({
            "task_kind": task_kind,
            "target_server_id": target_server_id,
            "payload": payload,
        })
        return f"tsk_{len(calls)}", False

    monkeypatch.setattr(
        "src.services.auto_inventory.worker_client.dispatch_task_with_hit",
        fake_dispatch_with_hit,
    )
    return calls


async def _make_managed(make_server, db, *, dept="dep_a"):
    srv = await make_server(department_id=dept)
    srv.is_managed = True
    srv.management_user = "dbos"
    await db.flush()
    return srv


@pytest.mark.usefixtures("soft_dept_mode")
class TestAutoRefreshAfterPrepare:
    """#2 — prepare-callback тянет за собой inventory.sync + power.status."""

    async def test_prepared_dispatches_inventory_and_power(
        self, client, worker_bot_token_a, make_server, db, dept_a, captured_dispatch,
    ):
        srv = await make_server(department_id=dept_a)
        resp = await client.post(
            f"{BASE_INT}/servers/{srv.id}/prepared",
            headers=_hdr(worker_bot_token_a, dept=dept_a),
            json={"management_user": "dbos"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["is_managed"] is True

        kinds = {c["task_kind"] for c in captured_dispatch}
        assert kinds == {"inventory.sync", "power.status"}
        assert all(c["target_server_id"] == srv.id for c in captured_dispatch)
        # power.status payload несёт host/ssh_port для reachability-пробы.
        power = next(c for c in captured_dispatch if c["task_kind"] == "power.status")
        assert power["payload"]["host"] == srv.hostname
        assert power["payload"]["ssh_port"] == srv.ssh_port

    async def test_prepared_stays_ok_when_dispatch_fails(
        self, client, worker_bot_token_a, make_server, db, dept_a, monkeypatch,
    ):
        """Best-effort: фейл авто-диспатча не валит prepare-callback."""
        srv = await make_server(department_id=dept_a)

        async def boom(*a, **k):
            raise RuntimeError("worker down")

        monkeypatch.setattr(
            "src.services.auto_inventory.worker_client.dispatch_task_with_hit", boom,
        )
        resp = await client.post(
            f"{BASE_INT}/servers/{srv.id}/prepared",
            headers=_hdr(worker_bot_token_a, dept=dept_a),
            json={"management_user": "dbos"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["is_managed"] is True


@pytest.mark.usefixtures("soft_dept_mode")
class TestAutoInventorySweep:
    """#3 — плановый прогон по всем managed-серверам."""

    async def test_sweep_dispatches_per_managed_server(
        self, client, worker_bot_token_a, make_server, db, captured_dispatch,
    ):
        m1 = await _make_managed(make_server, db)
        m2 = await _make_managed(make_server, db, dept="dep_b")
        # Неподготовленный — прогон его не трогает.
        await make_server(department_id="dep_a")
        # Decommissioned managed — тоже пропускается.
        dead = await _make_managed(make_server, db)
        dead.status = ServerStatus.DECOMMISSIONED
        await db.flush()

        resp = await client.post(
            f"{BASE_INT}/servers/auto-inventory-sweep",
            headers=_hdr(worker_bot_token_a),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["ok"] is True
        assert body["processed"] == 2
        assert body["dispatched_tasks"] == 4  # 2 сервера × (inventory + power)
        assert body["truncated"] == 0

        targets = {c["target_server_id"] for c in captured_dispatch}
        assert targets == {m1.id, m2.id}
        # По каждому серверу — обе задачи.
        for sid in (m1.id, m2.id):
            kinds = {c["task_kind"] for c in captured_dispatch if c["target_server_id"] == sid}
            assert kinds == {"inventory.sync", "power.status"}

    async def test_sweep_truncates_over_cap(
        self, client, worker_bot_token_a, make_server, db, captured_dispatch,
        monkeypatch,
    ):
        await _make_managed(make_server, db)
        await _make_managed(make_server, db)
        await _make_managed(make_server, db)
        monkeypatch.setattr(get_settings(), "auto_inventory_fanout_max", 1)

        resp = await client.post(
            f"{BASE_INT}/servers/auto-inventory-sweep",
            headers=_hdr(worker_bot_token_a),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total_managed"] == 3
        assert body["processed"] == 1
        assert body["dispatched_tasks"] == 2
        assert body["truncated"] == 2
        assert len(captured_dispatch) == 2

    async def test_sweep_empty_when_no_managed(
        self, client, worker_bot_token_a, captured_dispatch,
    ):
        resp = await client.post(
            f"{BASE_INT}/servers/auto-inventory-sweep",
            headers=_hdr(worker_bot_token_a),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["processed"] == 0
        assert body["dispatched_tasks"] == 0
        assert captured_dispatch == []

    async def test_sweep_admin_forbidden(
        self, client, admin_role_token_a, captured_dispatch,
    ):
        """prepare_callback — worker_bot-only грант; admin его не несёт."""
        resp = await client.post(
            f"{BASE_INT}/servers/auto-inventory-sweep",
            headers=_hdr(admin_role_token_a),
        )
        assert_error(resp, 403, "PERMISSION_DENIED")
        assert captured_dispatch == []

    async def test_sweep_no_token_401(self, client):
        resp = await client.post(f"{BASE_INT}/servers/auto-inventory-sweep")
        assert_error(resp, 401, "ACCESS_TOKEN_MISSING")
