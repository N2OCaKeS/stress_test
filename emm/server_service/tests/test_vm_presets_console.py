"""Тесты VM-домена: пресеты (CRUD + права), create-default-vms (deploy-once +
ёмкость), autostart, teardown VMS-hub, консоль (ssh/vnc/serial).

`worker_client.dispatch_task` мочится — тесты фиксируют argument-shape (task_kind
/ payload) и HTTP-контракт, реального Redis/worker-БД нет.
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio

from tests._helpers import assert_error, auth_hdr as _hdr, make_dispatch_capture, next_stand_number

BASE = "/api/server/v1"


@pytest_asyncio.fixture
async def make_hub(db):
    """Сервер-hub в dep_a: is_managed + is_vms_hub + virtualization + ёмкость."""
    from src.models import Server, ServerDisk
    from src.utils.ids import _new_id, server_id as new_id

    async def _factory(
        *, department_id: str = "dep_a", is_vms_hub: bool = True,
        cpu_threads: int = 16, ram_total_mb: int = 32768, disk_gb: int = 1000,
    ) -> Server:
        suffix = uuid.uuid4().hex[:6]
        srv = Server(
            id=new_id(),
            hostname=f"hub-{suffix}",
            ip_address=f"10.30.{int(suffix[:2], 16) % 256}.{int(suffix[2:4], 16) % 256}",
            ssh_port=22,
            department_id=department_id,
            is_managed=True,
            is_vms_hub=is_vms_hub,
            virtualization=True,
            cpu_threads=cpu_threads,
            ram_total_mb=ram_total_mb,
            network_interface_name="eth0",
            number=next_stand_number(),
        )
        db.add(srv)
        await db.flush()
        db.add(ServerDisk(
            id=_new_id("dsk_"), server_id=srv.id,
            device_name="system", size_gb=disk_gb, is_system=True,
        ))
        await db.flush()
        return srv

    return _factory


@pytest_asyncio.fixture
async def make_vm(db):
    """ВМ в БД напрямую (для teardown/console-тестов)."""
    from src.models import Vm
    from src.utils.ids import vm_id as new_id

    async def _factory(
        *, hub, department_id: str = "dep_a", name: str | None = None,
        status: str = "free", cpu: int = 4, ram_mb: int = 8192, disk_gb: int = 100,
        ip_address: str | None = None, mgmt_user: str | None = None,
    ) -> Vm:
        vm = Vm(
            id=new_id(),
            name=name or f"vm-{uuid.uuid4().hex[:6]}",
            number=next_stand_number(),
            hub_server_id=hub.id,
            department_id=department_id,
            status=status,
            cpu=cpu, ram_mb=ram_mb, disk_gb=disk_gb,
            ip_address=ip_address,
            mgmt_user=mgmt_user,
        )
        db.add(vm)
        await db.flush()
        await db.refresh(vm)
        return vm

    return _factory


async def _make_preset(client, token, *, name, network_mode="bridge", **over) -> dict:
    body = {
        "name": name,
        "department_id": "dep_a",
        "cpu": 4, "ram_mb": 8192, "disk_gb": 100,
        "network_mode": network_mode,
    }
    body.update(over)
    resp = await client.post(f"{BASE}/vm-presets", json=body, headers=_hdr(token))
    assert resp.status_code == 201, resp.text
    return resp.json()


# ── пресеты: CRUD + права ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_preset_crud(client, admin_role_token_a):
    created = await _make_preset(client, admin_role_token_a, name="std-a", number=101)
    assert created["id"].startswith("vps_")
    assert created["network_mode"] == "bridge"

    # list
    resp = await client.get(f"{BASE}/vm-presets", headers=_hdr(admin_role_token_a))
    assert resp.status_code == 200
    assert any(i["id"] == created["id"] for i in resp.json()["items"])

    # get
    resp = await client.get(f"{BASE}/vm-presets/{created['id']}", headers=_hdr(admin_role_token_a))
    assert resp.status_code == 200
    assert resp.json()["number"] == 101

    # update
    resp = await client.patch(
        f"{BASE}/vm-presets/{created['id']}", json={"cpu": 8},
        headers=_hdr(admin_role_token_a),
    )
    assert resp.status_code == 200
    assert resp.json()["cpu"] == 8

    # delete
    resp = await client.delete(f"{BASE}/vm-presets/{created['id']}", headers=_hdr(admin_role_token_a))
    assert resp.status_code == 204
    resp = await client.get(f"{BASE}/vm-presets/{created['id']}", headers=_hdr(admin_role_token_a))
    assert_error(resp, 404, "VM_PRESET_NOT_FOUND")


@pytest.mark.asyncio
async def test_preset_duplicate(client, admin_role_token_a):
    await _make_preset(client, admin_role_token_a, name="dup")
    resp = await client.post(
        f"{BASE}/vm-presets",
        json={"name": "dup", "department_id": "dep_a", "cpu": 2, "ram_mb": 4096, "disk_gb": 50},
        headers=_hdr(admin_role_token_a),
    )
    assert_error(resp, 409, "VM_PRESET_DUPLICATE")


@pytest.mark.asyncio
async def test_preset_manage_denied_for_guest(client, guest_token_a):
    resp = await client.post(
        f"{BASE}/vm-presets",
        json={"name": "nope", "department_id": "dep_a", "cpu": 2, "ram_mb": 4096, "disk_gb": 50},
        headers=_hdr(guest_token_a),
    )
    assert resp.status_code == 403
    resp = await client.get(f"{BASE}/vm-presets", headers=_hdr(guest_token_a))
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_preset_cross_dept_isolation(client, admin_role_token_a):
    resp = await client.post(
        f"{BASE}/vm-presets",
        json={"name": "x", "department_id": "dep_b", "cpu": 2, "ram_mb": 4096, "disk_gb": 50},
        headers=_hdr(admin_role_token_a),
    )
    assert_error(resp, 409, "DEPARTMENT_ISOLATION")


# ── create-default-vms: deploy-once + ёмкость ────────────────────────────────


@pytest.mark.asyncio
async def test_create_default_deploys_presets(
    client, admin_role_token_a, make_hub, monkeypatch, db,
):
    calls = make_dispatch_capture(monkeypatch)
    await _make_preset(client, admin_role_token_a, name="br-station", network_mode="bridge")
    await _make_preset(client, admin_role_token_a, name="nat-station", network_mode="nat")
    hub = await make_hub()

    resp = await client.post(
        f"{BASE}/servers/{hub.id}/create-default-vms", headers=_hdr(admin_role_token_a),
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert len(body["created"]) == 2
    assert body["skipped"] == []
    assert {c["task_kind"] for c in calls} == {"vm.create"}
    assert len(calls) == 2

    # Пресеты выше созданы без явного `number` — карточка ВМ всё равно не
    # может остаться без номера стенда (NOT NULL), сервис обязан
    # авто-назначить его и не столкнуть лбами две ВМ одного батча.
    from src.models import Vm
    from sqlalchemy import select

    vm_ids = [c["vm_id"] for c in body["created"]]
    rows = (await db.execute(select(Vm).where(Vm.id.in_(vm_ids)))).scalars().all()
    numbers = [row.number for row in rows]
    assert all(n is not None for n in numbers)
    assert len(set(numbers)) == len(numbers)


@pytest.mark.asyncio
async def test_create_default_repeat_conflict(client, admin_role_token_a, make_hub, monkeypatch):
    make_dispatch_capture(monkeypatch)
    await _make_preset(client, admin_role_token_a, name="br-only", network_mode="bridge")
    hub = await make_hub()
    resp = await client.post(f"{BASE}/servers/{hub.id}/create-default-vms", headers=_hdr(admin_role_token_a))
    assert resp.status_code == 202, resp.text
    # повтор — всё уже развёрнуто
    resp = await client.post(f"{BASE}/servers/{hub.id}/create-default-vms", headers=_hdr(admin_role_token_a))
    assert_error(resp, 409, "VM_PRESETS_ALREADY_DEPLOYED")


@pytest.mark.asyncio
async def test_create_default_bridge_global_nat_per_hub(
    client, admin_role_token_a, make_hub, monkeypatch,
):
    make_dispatch_capture(monkeypatch)
    await _make_preset(client, admin_role_token_a, name="br-glob", network_mode="bridge")
    await _make_preset(client, admin_role_token_a, name="nat-perhub", network_mode="nat")
    hub1 = await make_hub()
    hub2 = await make_hub()

    resp = await client.post(f"{BASE}/servers/{hub1.id}/create-default-vms", headers=_hdr(admin_role_token_a))
    assert resp.status_code == 202
    assert len(resp.json()["created"]) == 2

    # На hub2: bridge уже развёрнут глобально → skipped; nat разворачивается снова.
    resp = await client.post(f"{BASE}/servers/{hub2.id}/create-default-vms", headers=_hdr(admin_role_token_a))
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert len(body["created"]) == 1
    assert body["created"][0]["name"] == "nat-perhub"
    assert len(body["skipped"]) == 1
    assert body["skipped"][0]["name"] == "br-glob"
    assert body["skipped"][0]["reason"] == "already_deployed_global"


@pytest.mark.asyncio
async def test_create_default_capacity(client, admin_role_token_a, make_hub, monkeypatch):
    make_dispatch_capture(monkeypatch)
    await _make_preset(client, admin_role_token_a, name="huge", cpu=32)
    hub = await make_hub(cpu_threads=16)
    resp = await client.post(f"{BASE}/servers/{hub.id}/create-default-vms", headers=_hdr(admin_role_token_a))
    assert_error(resp, 409, "VM_CAPACITY_EXCEEDED")


@pytest.mark.asyncio
async def test_create_default_no_presets(client, admin_role_token_a, make_hub, monkeypatch):
    make_dispatch_capture(monkeypatch)
    hub = await make_hub()
    resp = await client.post(f"{BASE}/servers/{hub.id}/create-default-vms", headers=_hdr(admin_role_token_a))
    assert_error(resp, 404, "VM_NO_PRESETS")


# ── autostart ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_autostart_dispatches(client, admin_role_token_a, make_hub, make_vm, monkeypatch):
    calls = make_dispatch_capture(monkeypatch)
    hub = await make_hub()
    vm = await make_vm(hub=hub)
    resp = await client.post(
        f"{BASE}/vms/{vm.id}/autostart", json={"enabled": True},
        headers=_hdr(admin_role_token_a),
    )
    assert resp.status_code == 202, resp.text
    assert len(calls) == 1
    assert calls[0]["task_kind"] == "vm.set_autostart"
    assert calls[0]["payload"]["autostart"] is True
    assert calls[0]["payload"]["vm_id"] == vm.id

    resp = await client.get(f"{BASE}/vms/{vm.id}", headers=_hdr(admin_role_token_a))
    assert resp.json()["autostart"] is True


# ── teardown VMS-hub ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_teardown_cleans_db_and_dispatches(
    client, admin_role_token_a, make_hub, make_vm, monkeypatch,
):
    calls = make_dispatch_capture(monkeypatch)
    hub = await make_hub()
    vm1 = await make_vm(hub=hub, name="station-a")
    vm2 = await make_vm(hub=hub, name="station-b")

    resp = await client.delete(f"{BASE}/servers/{hub.id}/vms-hub", headers=_hdr(admin_role_token_a))
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["vms_removed"] == 2
    assert calls[0]["task_kind"] == "vms_hub.teardown"
    assert calls[0]["target_server_id"] == hub.id
    # payload несёт имена ВМ (собраны ДО удаления карточек) + os_family + пул.
    payload = calls[0]["payload"]
    assert sorted(payload["vms"]) == ["station-a", "station-b"]
    assert payload["os_family"] == "apt"
    assert payload["storage_pool_path"] == "/vms"

    # ВМ снесены из БД, hub больше не VMS-hub.
    for vm in (vm1, vm2):
        resp = await client.get(f"{BASE}/vms/{vm.id}", headers=_hdr(admin_role_token_a))
        assert_error(resp, 404, "VM_NOT_FOUND")
    resp = await client.post(f"{BASE}/servers/{hub.id}/create-default-vms", headers=_hdr(admin_role_token_a))
    # hub уже не VMS-hub → HUB_NOT_PREPARED
    assert_error(resp, 409, "HUB_NOT_PREPARED")


@pytest.mark.asyncio
async def test_teardown_not_a_hub(client, admin_role_token_a, make_hub, monkeypatch):
    make_dispatch_capture(monkeypatch)
    hub = await make_hub(is_vms_hub=False)
    resp = await client.delete(f"{BASE}/servers/{hub.id}/vms-hub", headers=_hdr(admin_role_token_a))
    assert_error(resp, 409, "NOT_A_VMS_HUB")


# ── консоль ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_console_ssh(client, admin_role_token_a, make_hub, make_vm):
    hub = await make_hub()
    vm = await make_vm(hub=hub, ip_address="10.30.0.55", mgmt_user="dbos")
    resp = await client.post(
        f"{BASE}/vms/{vm.id}/console", json={"kind": "ssh"}, headers=_hdr(admin_role_token_a),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["kind"] == "ssh"
    assert body["token"].startswith("vmc_")
    assert body["host"] == "10.30.0.55"
    assert body["port"] == 22
    assert body["username"] == "dbos"
    assert body["expires_in"] > 0


@pytest.mark.asyncio
async def test_console_vnc(client, admin_role_token_a, make_hub, make_vm):
    hub = await make_hub()
    vm = await make_vm(hub=hub)
    resp = await client.post(
        f"{BASE}/vms/{vm.id}/console", json={"kind": "vnc"}, headers=_hdr(admin_role_token_a),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["kind"] == "vnc"
    assert body["host"] == str(hub.ip_address)
    assert body["ws_path"] == f"/vm-console/vnc/{vm.id}"


@pytest.mark.asyncio
async def test_console_invalid_kind(client, admin_role_token_a, make_hub, make_vm):
    hub = await make_hub()
    vm = await make_vm(hub=hub)
    resp = await client.post(
        f"{BASE}/vms/{vm.id}/console", json={"kind": "telnet"}, headers=_hdr(admin_role_token_a),
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_console_reserved_by_other(client, admin_role_token_a, make_hub, make_vm, make_token):
    hub = await make_hub()
    vm = await make_vm(hub=hub, status="someone_else")
    # не-админ (guest) забронированной другим ВМ — консоль недоступна.
    guest = make_token(department_id="dep_a", service_roles={"server_service": ["guest"]}, username="joe")
    resp = await client.post(
        f"{BASE}/vms/{vm.id}/console", json={"kind": "vnc"}, headers=_hdr(guest),
    )
    assert_error(resp, 409, "VM_RESERVED")
