"""Тесты батч-фичи ВМ: учётки↔ВМ (join), hostname, POST /vms/bulk,
снимки с mode/kind/поиском, min_disk_gb в каталоге образов.

`worker_client.dispatch_task` мочится — фиксируем HTTP-контракт и argument-shape.
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select

from tests._helpers import assert_error, auth_hdr as _hdr, make_dispatch_capture

BASE = "/api/server/v1"


@pytest_asyncio.fixture
async def make_hub(db):
    """Сервер-hub в dep_a: is_managed + is_vms_hub + virtualization + ёмкость."""
    from src.models import Server, ServerDisk
    from src.utils.ids import _new_id, server_id as new_id

    async def _factory(
        *, department_id: str = "dep_a", is_vms_hub: bool = True,
        cpu_threads: int = 64, ram_total_mb: int = 131072, disk_gb: int = 2000,
    ) -> Server:
        suffix = uuid.uuid4().hex[:6]
        srv = Server(
            id=new_id(),
            hostname=f"hub-{suffix}",
            ip_address=f"10.20.{int(suffix[:2], 16) % 256}.{int(suffix[2:4], 16) % 256}",
            ssh_port=22,
            department_id=department_id,
            is_managed=True,
            is_vms_hub=is_vms_hub,
            virtualization=True,
            cpu_threads=cpu_threads,
            ram_total_mb=ram_total_mb,
            network_interface_name="eth0",
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


def _create_body(hub, **over) -> dict:
    body = {
        "hub_server_id": hub.id,
        "name": f"vm-{uuid.uuid4().hex[:6]}",
        "department_id": "dep_a",
        "cpu": 4, "ram_mb": 8192, "disk_gb": 100,
        "network_mode": "nat",
    }
    body.update(over)
    return body


# ── учётки ↔ ВМ: create с accounts + hostname ────────────────────────────────


@pytest.mark.asyncio
async def test_create_with_accounts_and_hostname(
    client, admin_role_token_a, make_hub, make_server, make_account, db, monkeypatch,
):
    from src.models import ServerAccountVm

    calls = make_dispatch_capture(monkeypatch)
    hub = await make_hub()
    srv = await make_server(department_id="dep_a")
    acc = await make_account(server_id=srv.id, login="alice")

    resp = await client.post(
        f"{BASE}/vms",
        json=_create_body(hub, hostname="my-host", accounts=[acc.id]),
        headers=_hdr(admin_role_token_a),
    )
    assert resp.status_code == 202, resp.text
    vm_id = resp.json()["vm_id"]

    # link-строка появилась
    links = (await db.execute(
        select(ServerAccountVm).where(ServerAccountVm.vm_id == vm_id)
    )).scalars().all()
    assert [link.login for link in links] == ["alice"]

    # payload воркеру несёт hostname и accounts
    payload = calls[0]["payload"]
    assert payload["hostname"] == "my-host"
    assert payload["accounts"] == [{
        "account_id": acc.id, "login": "alice",
        "has_sudo": False, "unix_groups": [], "ssh_public_key": None,
    }]


@pytest.mark.asyncio
async def test_create_hostname_defaults_to_name_in_payload(
    client, admin_role_token_a, make_hub, monkeypatch,
):
    calls = make_dispatch_capture(monkeypatch)
    hub = await make_hub()
    body = _create_body(hub, name="vm-noname")
    resp = await client.post(f"{BASE}/vms", json=body, headers=_hdr(admin_role_token_a))
    assert resp.status_code == 202, resp.text
    assert calls[0]["payload"]["hostname"] == "vm-noname"


@pytest.mark.asyncio
async def test_create_account_cross_dept_forbidden(
    client, admin_role_token_a, make_hub, make_server, make_account, monkeypatch,
):
    make_dispatch_capture(monkeypatch)
    hub = await make_hub()
    srv_b = await make_server(department_id="dep_b")
    acc_b = await make_account(server_id=srv_b.id, login="bob")
    resp = await client.post(
        f"{BASE}/vms",
        json=_create_body(hub, accounts=[acc_b.id]),
        headers=_hdr(admin_role_token_a),
    )
    assert_error(resp, 403, "VM_ACCOUNT_FORBIDDEN")


@pytest.mark.asyncio
async def test_create_account_not_found(
    client, admin_role_token_a, make_hub, monkeypatch,
):
    make_dispatch_capture(monkeypatch)
    hub = await make_hub()
    resp = await client.post(
        f"{BASE}/vms",
        json=_create_body(hub, accounts=["acc_doesnotexist"]),
        headers=_hdr(admin_role_token_a),
    )
    assert_error(resp, 404, "ACCOUNT_NOT_FOUND")


@pytest.mark.asyncio
async def test_create_duplicate_account_login_on_vm(
    client, admin_role_token_a, make_hub, make_server, make_account, monkeypatch,
):
    make_dispatch_capture(monkeypatch)
    hub = await make_hub()
    srv1 = await make_server(department_id="dep_a")
    srv2 = await make_server(department_id="dep_a")
    a1 = await make_account(server_id=srv1.id, login="root")
    a2 = await make_account(server_id=srv2.id, login="root")
    resp = await client.post(
        f"{BASE}/vms",
        json=_create_body(hub, accounts=[a1.id, a2.id]),
        headers=_hdr(admin_role_token_a),
    )
    assert_error(resp, 409, "VM_ACCOUNT_DUPLICATE")


@pytest.mark.asyncio
async def test_account_vm_link_cascade_on_vm_delete(
    make_hub, make_server, make_account, db,
):
    from src.models import ServerAccountVm, Vm
    from src.repositories import server_account as account_repo
    from src.utils.ids import vm_id as new_vm_id

    hub = await make_hub()
    srv = await make_server(department_id="dep_a")
    acc = await make_account(server_id=srv.id, login="carol")
    vm = Vm(
        id=new_vm_id(), name=f"vm-{uuid.uuid4().hex[:6]}",
        hub_server_id=hub.id, department_id="dep_a",
        cpu=2, ram_mb=2048, disk_gb=20, network_mode="nat",
    )
    db.add(vm)
    await db.flush()
    await account_repo.add_vm_links(db, vm.id, [acc])
    await db.commit()

    vm_obj = (await db.execute(select(Vm).where(Vm.id == vm.id))).scalar_one()
    await db.delete(vm_obj)
    await db.commit()

    remaining = (await db.execute(
        select(ServerAccountVm).where(ServerAccountVm.vm_id == vm.id)
    )).scalars().all()
    assert remaining == []


@pytest.mark.asyncio
async def test_account_vm_link_cascade_on_account_delete(
    make_hub, make_server, make_account, db,
):
    from src.models import ServerAccount, ServerAccountVm, Vm
    from src.repositories import server_account as account_repo
    from src.utils.ids import vm_id as new_vm_id

    hub = await make_hub()
    srv = await make_server(department_id="dep_a")
    acc = await make_account(server_id=srv.id, login="dave")
    vm = Vm(
        id=new_vm_id(), name=f"vm-{uuid.uuid4().hex[:6]}",
        hub_server_id=hub.id, department_id="dep_a",
        cpu=2, ram_mb=2048, disk_gb=20, network_mode="nat",
    )
    db.add(vm)
    await db.flush()
    await account_repo.add_vm_links(db, vm.id, [acc])
    await db.commit()

    acc_obj = (await db.execute(
        select(ServerAccount).where(ServerAccount.id == acc.id)
    )).scalar_one()
    await db.delete(acc_obj)
    await db.commit()

    remaining = (await db.execute(
        select(ServerAccountVm).where(ServerAccountVm.vm_id == vm.id)
    )).scalars().all()
    assert remaining == []


# ── POST /vms/bulk ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_bulk_create_all_ok(
    client, admin_role_token_a, make_hub, monkeypatch,
):
    calls = make_dispatch_capture(monkeypatch)
    hub = await make_hub()
    body = {"items": [_create_body(hub), _create_body(hub)]}
    resp = await client.post(f"{BASE}/vms/bulk", json=body, headers=_hdr(admin_role_token_a))
    assert resp.status_code == 202, resp.text
    data = resp.json()
    assert data["created_count"] == 2
    assert data["error_count"] == 0
    assert all(r["status"] == "created" and r["vm_id"] for r in data["results"])
    # два независимых dispatch'а (без общего idempotency-ключа)
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_bulk_one_fails_others_continue(
    client, admin_role_token_a, make_hub, monkeypatch,
):
    make_dispatch_capture(monkeypatch)
    hub = await make_hub()
    good1 = _create_body(hub, name="ok-1")
    bad = _create_body(hub, name="bad", department_id="dep_b")  # DEPARTMENT_ISOLATION
    good2 = _create_body(hub, name="ok-2")
    body = {"items": [good1, bad, good2]}
    resp = await client.post(f"{BASE}/vms/bulk", json=body, headers=_hdr(admin_role_token_a))
    assert resp.status_code == 202, resp.text
    data = resp.json()
    assert data["created_count"] == 2
    assert data["error_count"] == 1
    results = data["results"]
    assert results[0]["status"] == "created"
    assert results[1]["status"] == "error"
    assert results[1]["index"] == 1
    assert results[1]["error_code"] == "DEPARTMENT_ISOLATION"
    assert results[2]["status"] == "created"


@pytest.mark.asyncio
async def test_bulk_capacity_accumulates(
    client, admin_role_token_a, make_hub, monkeypatch,
):
    make_dispatch_capture(monkeypatch)
    hub = await make_hub(cpu_threads=10)
    body = {"items": [
        _create_body(hub, name="c1", cpu=6),
        _create_body(hub, name="c2", cpu=6),  # 6+6 > 10 → упадёт по ёмкости
    ]}
    resp = await client.post(f"{BASE}/vms/bulk", json=body, headers=_hdr(admin_role_token_a))
    assert resp.status_code == 202, resp.text
    data = resp.json()
    assert data["created_count"] == 1
    assert data["error_count"] == 1
    assert data["results"][1]["error_code"] == "VM_CAPACITY_EXCEEDED"


@pytest.mark.asyncio
async def test_bulk_no_permission_403(
    client, no_role_token_a, make_hub, monkeypatch,
):
    make_dispatch_capture(monkeypatch)
    hub = await make_hub()
    body = {"items": [_create_body(hub)]}
    resp = await client.post(f"{BASE}/vms/bulk", json=body, headers=_hdr(no_role_token_a))
    assert resp.status_code == 403


# ── снимки: mode / kind / поиск (os_baseline виден, _build скрыт) ─────────────


@pytest_asyncio.fixture
async def make_snapshot(db):
    from src.models import VmSnapshot
    from src.utils.ids import vm_snapshot_id as new_id

    async def _factory(*, vm, name, kind="user", mode=None, os_version=None,
                       is_system=False, snapshot_type="disk_only"):
        snap = VmSnapshot(
            id=new_id(), vm_id=vm.id, name=name,
            snapshot_type=snapshot_type, kind=kind, mode=mode,
            os_version=os_version, is_system=is_system, state="ready",
        )
        db.add(snap)
        await db.flush()
        return snap

    return _factory


@pytest.mark.asyncio
async def test_snapshots_groups_and_hidden_build(
    client, admin_role_token_a, make_hub, make_snapshot, db,
):
    from src.models import Vm
    from src.utils.ids import vm_id as new_vm_id

    hub = await make_hub()
    vm = Vm(id=new_vm_id(), name="snap-vm", hub_server_id=hub.id,
            department_id="dep_a", cpu=2, ram_mb=2048, disk_gb=20, network_mode="nat")
    db.add(vm)
    await db.flush()
    await make_snapshot(vm=vm, name="1.8.1.6_oryol", kind="os_baseline",
                        mode="oryol", os_version="1.8.1.6")
    await make_snapshot(vm=vm, name="my-checkpoint", kind="user")
    await make_snapshot(vm=vm, name="1.8.1.6_orel_build", is_system=True,
                        kind="os_baseline")
    await db.commit()

    resp = await client.get(f"{BASE}/vms/{vm.id}/snapshots", headers=_hdr(admin_role_token_a))
    assert resp.status_code == 200, resp.text
    items = resp.json()
    names = {i["name"] for i in items}
    assert names == {"1.8.1.6_oryol", "my-checkpoint"}  # _build скрыт
    baseline = next(i for i in items if i["name"] == "1.8.1.6_oryol")
    assert baseline["kind"] == "os_baseline"
    assert baseline["mode"] == "oryol"
    assert baseline["os_version"] == "1.8.1.6"
    assert baseline["snapshot_type"] == "disk_only"


@pytest.mark.asyncio
async def test_snapshots_search_by_name(
    client, admin_role_token_a, make_hub, make_snapshot, db,
):
    from src.models import Vm
    from src.utils.ids import vm_id as new_vm_id

    hub = await make_hub()
    vm = Vm(id=new_vm_id(), name="snap-vm2", hub_server_id=hub.id,
            department_id="dep_a", cpu=2, ram_mb=2048, disk_gb=20, network_mode="nat")
    db.add(vm)
    await db.flush()
    await make_snapshot(vm=vm, name="alpha-one", kind="user")
    await make_snapshot(vm=vm, name="beta-two", kind="user")
    await db.commit()

    resp = await client.get(
        f"{BASE}/vms/{vm.id}/snapshots?q=alpha", headers=_hdr(admin_role_token_a),
    )
    assert resp.status_code == 200
    assert [i["name"] for i in resp.json()] == ["alpha-one"]


# ── min_disk_gb в каталоге образов ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_vm_images_expose_min_disk_gb(client, admin_role_token_a, db):
    from src.models import VmImage
    from src.utils.ids import vm_image_id as new_id

    db.add(VmImage(
        id=new_id(), name=f"box-{uuid.uuid4().hex[:6]}",
        url="ftp://x/box.tar.gz", kind="single", min_disk_gb=40,
    ))
    await db.commit()

    resp = await client.get(f"{BASE}/vm-images", headers=_hdr(admin_role_token_a))
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    assert any(i["min_disk_gb"] == 40 for i in items)


def test_parse_min_disk_gb_from_config():
    from src.services.vm_image_service import _parse_entry

    parsed = _parse_entry("vm_station", {
        "url": "ftp://x/s.tar.gz", "os_versions": ["1.7.5.9"], "min_disk_gb": "30",
    })
    assert parsed["min_disk_gb"] == 30
    # мусор → None
    bad = _parse_entry("box", {"url": "ftp://x/b.tar.gz", "min_disk_gb": "huge"})
    assert bad["min_disk_gb"] is None
