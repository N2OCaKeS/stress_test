"""Тесты VM-инвентаризации: POST /vms/{id}/inventory-sync и /users-inventory
(VM-аналоги серверных inventory.sync / users.inventory).

`worker_client.dispatch_task` мочится — тесты фиксируют dispatch-контракт
(task_kind, payload) и гейты готовности ВМ. Реального worker'а нет.
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio

from tests._helpers import assert_error, auth_hdr as _hdr, make_dispatch_capture, next_stand_number

BASE = "/api/server/v1"


@pytest_asyncio.fixture
async def make_hub(db):
    from src.models import Server, ServerDisk
    from src.utils.ids import _new_id, server_id as new_id

    async def _factory(*, department_id: str = "dep_a") -> Server:
        suffix = uuid.uuid4().hex[:6]
        srv = Server(
            id=new_id(),
            hostname=f"hub-{suffix}",
            ip_address=f"10.41.{int(suffix[:2], 16) % 256}.{int(suffix[2:4], 16) % 256}",
            ssh_port=22,
            department_id=department_id,
            is_managed=True,
            is_vms_hub=True,
            virtualization=True,
            cpu_threads=16,
            ram_total_mb=32768,
            network_interface_name="eth0",
            number=next_stand_number(),
        )
        db.add(srv)
        await db.flush()
        db.add(ServerDisk(
            id=_new_id("dsk_"), server_id=srv.id,
            device_name="system", size_gb=1000, is_system=True,
        ))
        await db.flush()
        return srv

    return _factory


@pytest_asyncio.fixture
async def make_vm(db):
    from src.models import Vm
    from src.utils.ids import vm_id as new_id

    async def _factory(
        *, hub, department_id: str = "dep_a", name: str | None = None,
        is_managed: bool = False, ip_address: str | None = None,
    ) -> Vm:
        vm = Vm(
            id=new_id(),
            name=name or f"vm-{uuid.uuid4().hex[:6]}",
            number=next_stand_number(),
            hub_server_id=hub.id,
            department_id=department_id,
            status="free",
            cpu=4, ram_mb=8192, disk_gb=100,
            is_managed=is_managed,
            ip_address=ip_address,
        )
        db.add(vm)
        await db.flush()
        await db.refresh(vm)
        return vm

    return _factory


# ── inventory-sync ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_inventory_sync_dispatches(
    client, admin_role_token_a, make_hub, make_vm, monkeypatch,
):
    calls = make_dispatch_capture(monkeypatch)
    hub = await make_hub()
    vm = await make_vm(hub=hub, is_managed=True, ip_address="10.41.0.77")
    resp = await client.post(
        f"{BASE}/vms/{vm.id}/inventory-sync", headers=_hdr(admin_role_token_a),
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["task_id"] is not None
    assert len(calls) == 1
    assert calls[0]["task_kind"] == "vm.inventory_sync"
    assert calls[0]["target_server_id"] == hub.id
    assert calls[0]["payload"]["host"] == str(hub.ip_address)
    assert calls[0]["payload"]["guest_ip"] == "10.41.0.77"
    assert calls[0]["payload"]["vm_id"] == vm.id


@pytest.mark.asyncio
async def test_inventory_sync_requires_prepared(
    client, admin_role_token_a, make_hub, make_vm, monkeypatch,
):
    make_dispatch_capture(monkeypatch)
    hub = await make_hub()
    vm = await make_vm(hub=hub, is_managed=False, ip_address="10.41.0.78")
    resp = await client.post(
        f"{BASE}/vms/{vm.id}/inventory-sync", headers=_hdr(admin_role_token_a),
    )
    assert_error(resp, 409, "VM_PREPARE_REQUIRED")


@pytest.mark.asyncio
async def test_inventory_sync_requires_guest_ip(
    client, admin_role_token_a, make_hub, make_vm, monkeypatch,
):
    make_dispatch_capture(monkeypatch)
    hub = await make_hub()
    vm = await make_vm(hub=hub, is_managed=True, ip_address=None)
    resp = await client.post(
        f"{BASE}/vms/{vm.id}/inventory-sync", headers=_hdr(admin_role_token_a),
    )
    assert_error(resp, 409, "VM_GUEST_IP_UNKNOWN")


@pytest.mark.asyncio
async def test_inventory_sync_denied_without_vm_prepare(
    client, make_token, make_hub, make_vm, monkeypatch,
):
    make_dispatch_capture(monkeypatch)
    hub = await make_hub()
    vm = await make_vm(hub=hub, is_managed=True, ip_address="10.41.0.79")
    no_role = make_token(department_id="dep_a", service_roles={}, username="nobody")
    resp = await client.post(
        f"{BASE}/vms/{vm.id}/inventory-sync", headers=_hdr(no_role),
    )
    assert resp.status_code == 403


# ── users-inventory ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_users_inventory_dispatches(
    client, admin_role_token_a, make_hub, make_vm, monkeypatch,
):
    calls = make_dispatch_capture(monkeypatch)
    hub = await make_hub()
    vm = await make_vm(hub=hub, is_managed=True, ip_address="10.41.0.80")
    resp = await client.post(
        f"{BASE}/vms/{vm.id}/users-inventory", headers=_hdr(admin_role_token_a),
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["task_id"] is not None
    assert len(calls) == 1
    assert calls[0]["task_kind"] == "vm.users_inventory"
    assert calls[0]["payload"]["guest_ip"] == "10.41.0.80"
    assert calls[0]["payload"]["vm_id"] == vm.id


@pytest.mark.asyncio
async def test_users_inventory_requires_prepared(
    client, admin_role_token_a, make_hub, make_vm, monkeypatch,
):
    make_dispatch_capture(monkeypatch)
    hub = await make_hub()
    vm = await make_vm(hub=hub, is_managed=False, ip_address="10.41.0.81")
    resp = await client.post(
        f"{BASE}/vms/{vm.id}/users-inventory", headers=_hdr(admin_role_token_a),
    )
    assert_error(resp, 409, "VM_PREPARE_REQUIRED")
