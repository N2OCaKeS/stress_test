"""Частый статус-sweep ВМ: `POST /internal/vms/status-sweep`.

Зеркало серверного power-sweep'а (`test_auto_inventory.TestPowerSweep`), но по
ВМ: воркер даёт лишь расписание, а фан-аут (список всех активных ВМ + dispatch
`vm.status` на каждую) идёт в server_service. Проверяем per-VM dispatch, cap-
throttle, пропуск ВМ на списанном hub'е и worker_bot-грант.
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio

from src.core.config import get_settings
from src.core.constants import ServerStatus
from tests._helpers import assert_error, auth_hdr as _hdr

BASE_INT = "/api/server/v1/internal"


@pytest.fixture
def captured_dispatch(monkeypatch):
    """Перехват `dispatch_task_with_hit` в vm-сервисе (без cross-DB INSERT)."""
    calls: list[dict] = []

    async def fake_dispatch_with_hit(
        *, db, task_kind, target_server_id, payload, created_by,
        request_id, target_resource_id=None, idempotency_key=None, **kw,
    ):
        calls.append({
            "task_kind": task_kind,
            "target_server_id": target_server_id,
            "target_resource_id": target_resource_id,
            "payload": payload,
        })
        return f"tsk_{len(calls)}", False

    monkeypatch.setattr(
        "src.services.vm.worker_client.dispatch_task_with_hit",
        fake_dispatch_with_hit,
    )
    return calls


@pytest_asyncio.fixture
async def make_vm(db, make_server):
    """ВМ в БД, привязанная к hub-серверу (создаётся, если не передан)."""
    from src.models import Vm
    from src.utils.ids import vm_id as new_id

    async def _factory(
        *, hub=None, department_id: str = "dep_a",
        ip_address: str | None = None, busy_state: str | None = None,
    ) -> Vm:
        if hub is None:
            hub = await make_server(department_id=department_id)
        vm = Vm(
            id=new_id(),
            name=f"vm-{uuid.uuid4().hex[:6]}",
            hub_server_id=hub.id,
            department_id=department_id,
            ip_address=ip_address,
            busy_state=busy_state,
        )
        db.add(vm)
        await db.flush()
        await db.refresh(vm)
        return vm

    return _factory


@pytest.mark.usefixtures("soft_dept_mode")
class TestVmStatusSweep:
    async def test_sweep_dispatches_vm_status_per_vm(
        self, client, worker_bot_token_a, make_server, make_vm, db,
        captured_dispatch,
    ):
        hub = await make_server(department_id="dep_a")
        vm1 = await make_vm(hub=hub, ip_address="10.177.103.101")
        vm2 = await make_vm(hub=hub)  # без IP (NAT) — тоже пробуется
        await db.flush()

        resp = await client.post(
            f"{BASE_INT}/vms/status-sweep",
            headers=_hdr(worker_bot_token_a),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["ok"] is True
        assert body["total_vms"] == 2
        assert body["processed"] == 2
        assert body["dispatched_tasks"] == 2
        assert body["truncated"] == 0

        kinds = {c["task_kind"] for c in captured_dispatch}
        assert kinds == {"vm.status"}
        targets = {c["target_server_id"] for c in captured_dispatch}
        assert targets == {hub.id}  # SSH-таргет — всегда hub
        resources = {c["target_resource_id"] for c in captured_dispatch}
        assert resources == {vm1.id, vm2.id}
        # payload несёт vm_id/vm_name + hub-адресацию; guest_ip только у bridge-ВМ.
        by_vm = {c["target_resource_id"]: c["payload"] for c in captured_dispatch}
        assert by_vm[vm1.id]["guest_ip"] == "10.177.103.101"
        assert "host" in by_vm[vm1.id] and "vm_name" in by_vm[vm1.id]
        assert "guest_ip" not in by_vm[vm2.id]

    async def test_sweep_skips_vm_on_decommissioned_hub(
        self, client, worker_bot_token_a, make_server, make_vm, db,
        captured_dispatch,
    ):
        live_hub = await make_server(department_id="dep_a")
        await make_vm(hub=live_hub, ip_address="10.177.103.101")
        dead_hub = await make_server(department_id="dep_a")
        dead_hub.status = ServerStatus.DECOMMISSIONED
        await make_vm(hub=dead_hub)
        await db.flush()

        resp = await client.post(
            f"{BASE_INT}/vms/status-sweep",
            headers=_hdr(worker_bot_token_a),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # Обе ВМ существуют (total), но у ВМ на списанном hub'е dispatch не идёт.
        assert body["total_vms"] == 2
        assert body["processed"] == 1
        assert body["dispatched_tasks"] == 1
        assert len(captured_dispatch) == 1

    async def test_sweep_truncates_over_cap(
        self, client, worker_bot_token_a, make_server, make_vm, db,
        captured_dispatch, monkeypatch,
    ):
        hub = await make_server(department_id="dep_a")
        await make_vm(hub=hub)
        await make_vm(hub=hub)
        await make_vm(hub=hub)
        await db.flush()
        monkeypatch.setattr(get_settings(), "auto_inventory_fanout_max", 1)

        resp = await client.post(
            f"{BASE_INT}/vms/status-sweep",
            headers=_hdr(worker_bot_token_a),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total_vms"] == 3
        assert body["processed"] == 1
        assert body["dispatched_tasks"] == 1
        assert body["truncated"] == 2
        assert len(captured_dispatch) == 1

    async def test_sweep_empty_when_no_vms(
        self, client, worker_bot_token_a, captured_dispatch,
    ):
        resp = await client.post(
            f"{BASE_INT}/vms/status-sweep",
            headers=_hdr(worker_bot_token_a),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total_vms"] == 0
        assert body["processed"] == 0
        assert body["dispatched_tasks"] == 0
        assert captured_dispatch == []

    async def test_sweep_admin_forbidden(
        self, client, admin_role_token_a, captured_dispatch,
    ):
        resp = await client.post(
            f"{BASE_INT}/vms/status-sweep",
            headers=_hdr(admin_role_token_a),
        )
        assert_error(resp, 403, "PERMISSION_DENIED")
        assert captured_dispatch == []

    async def test_sweep_no_token_401(self, client):
        resp = await client.post(f"{BASE_INT}/vms/status-sweep")
        assert_error(resp, 401, "ACCESS_TOKEN_MISSING")
