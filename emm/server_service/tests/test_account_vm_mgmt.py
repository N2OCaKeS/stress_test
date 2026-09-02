"""Учётки общего пула на ВМ: привязка/отвязка (`server_account_vms`),
provision/update/deprovision в госте (dispatch), re-provision при ротации и
reveal пароля для ВМ-привязанной учётки.

Тот же `server_account`, что живёт на серверах, целится в ВМ через join
`server_account_vms`. `worker_client.dispatch_task[_with_hit]` мочится —
фиксируем HTTP-контракт и argument-shape, реального worker'а/Redis нет.
Серверный путь учёток тесты не трогают (он проверяется отдельно).
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select

from src.models import ServerAccountVm
from tests._helpers import assert_error, auth_hdr as _hdr, make_dispatch_capture

BASE = "/api/server/v1/server-accounts"


# ── фикстуры ─────────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def make_hub(db):
    """Сервер-hub в dep_a (is_managed + is_vms_hub + virtualization)."""
    from src.models import Server, ServerDisk
    from src.utils.ids import _new_id, server_id as new_id

    async def _factory(*, department_id: str = "dep_a"):
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
            management_user="dbos",
            cpu_threads=16,
            ram_total_mb=32768,
            network_interface_name="eth0",
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
    """ВМ в БД напрямую."""
    from src.models import Vm
    from src.utils.ids import vm_id as new_id

    async def _factory(
        *, hub, department_id: str = "dep_a", name: str | None = None,
        ip_address: str | None = "10.41.0.50", is_managed: bool = True,
    ):
        vm = Vm(
            id=new_id(),
            name=name or f"vm-{uuid.uuid4().hex[:6]}",
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


@pytest_asyncio.fixture
async def make_pool_account(db):
    """Учётка общего пула (dep_a, с паролем), опц. уже привязанная к ВМ."""
    from src.models import ServerAccount
    from src.services import secrets_service
    from src.utils.ids import server_account_id, server_account_vm_id

    async def _factory(
        *, login: str = "poolu", department_id: str = "dep_a",
        password: str | None = "pool-plaintext-pwd",
        has_sudo: bool = False, unix_groups: list[str] | None = None,
        vm=None, present_on_vm: bool = True,
    ):
        acc_id = server_account_id()
        acc = ServerAccount(
            id=acc_id,
            department_id=department_id,
            login=login,
            password_encrypted=secrets_service.encrypt(
                password,
                aad=secrets_service.aad_for_server_account_password(acc_id),
            ) if password else None,
            has_sudo=has_sudo,
            unix_groups=list(unix_groups) if unix_groups is not None else [],
        )
        db.add(acc)
        await db.flush()
        if vm is not None:
            db.add(ServerAccountVm(
                id=server_account_vm_id(),
                account_id=acc_id, vm_id=vm.id, login=login,
                present_on_vm=present_on_vm,
            ))
            await db.flush()
        await db.refresh(acc)
        return acc

    return _factory


async def _vm_link_count(db, account_id: str, vm_id: str) -> int:
    rows = (await db.execute(
        select(ServerAccountVm).where(
            ServerAccountVm.account_id == account_id,
            ServerAccountVm.vm_id == vm_id,
        )
    )).scalars().all()
    return len(rows)


# ── привязка / отвязка ───────────────────────────────────────────────────────


class TestAttachDetach:
    async def test_attach_inserts_link(
        self, client, db, admin_role_token_a, make_hub, make_vm, make_pool_account,
    ):
        hub = await make_hub()
        vm = await make_vm(hub=hub)
        acc = await make_pool_account()
        resp = await client.post(
            f"{BASE}/{acc.id}/vms", json={"vm_ids": [vm.id]},
            headers=_hdr(admin_role_token_a),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["account_id"] == acc.id
        assert body["vm_ids"] == [vm.id]
        assert await _vm_link_count(db, acc.id, vm.id) == 1

    async def test_attach_idempotent(
        self, client, admin_role_token_a, make_hub, make_vm, make_pool_account,
    ):
        hub = await make_hub()
        vm = await make_vm(hub=hub)
        acc = await make_pool_account(vm=vm)
        resp = await client.post(
            f"{BASE}/{acc.id}/vms", json={"vm_ids": [vm.id]},
            headers=_hdr(admin_role_token_a),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["vm_ids"] == [vm.id]

    async def test_attach_cross_dept_vm_404(
        self, client, admin_role_token_a, make_hub, make_vm, make_pool_account,
    ):
        hub = await make_hub(department_id="dep_b")
        vm = await make_vm(hub=hub, department_id="dep_b")
        acc = await make_pool_account()
        resp = await client.post(
            f"{BASE}/{acc.id}/vms", json={"vm_ids": [vm.id]},
            headers=_hdr(admin_role_token_a),
        )
        assert_error(resp, 404, "VM_NOT_FOUND")

    async def test_attach_with_provision_dispatches(
        self, client, admin_role_token_a, make_hub, make_vm, make_pool_account, monkeypatch,
    ):
        calls = make_dispatch_capture(monkeypatch)
        hub = await make_hub()
        vm = await make_vm(hub=hub, ip_address="10.41.0.77")
        acc = await make_pool_account(login="devops", has_sudo=True)
        resp = await client.post(
            f"{BASE}/{acc.id}/vms?provision=true", json={"vm_ids": [vm.id]},
            headers=_hdr(admin_role_token_a),
        )
        assert resp.status_code == 200, resp.text
        prov = [c for c in calls if c["task_kind"] == "vm.account_provision"]
        assert len(prov) == 1
        call = prov[0]
        assert call["target_server_id"] == hub.id
        assert call["target_resource_id"] == acc.id
        assert call["payload"]["vm_id"] == vm.id
        assert call["payload"]["guest_ip"] == "10.41.0.77"
        assert call["payload"]["host"] == str(hub.ip_address)
        assert call["payload"]["login"] == "devops"
        assert call["payload"]["has_sudo"] is True
        assert call["payload"]["account_id"] == acc.id

    async def test_detach_removes_link(
        self, client, db, admin_role_token_a, make_hub, make_vm, make_pool_account,
    ):
        hub = await make_hub()
        vm = await make_vm(hub=hub)
        acc = await make_pool_account(vm=vm)
        resp = await client.request(
            "DELETE", f"{BASE}/{acc.id}/vms/{vm.id}",
            headers=_hdr(admin_role_token_a),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["vm_ids"] == []
        assert await _vm_link_count(db, acc.id, vm.id) == 0

    async def test_detach_with_deprovision_dispatches(
        self, client, admin_role_token_a, make_hub, make_vm, make_pool_account, monkeypatch,
    ):
        calls = make_dispatch_capture(monkeypatch)
        hub = await make_hub()
        vm = await make_vm(hub=hub, ip_address="10.41.0.88")
        acc = await make_pool_account(vm=vm, present_on_vm=True)
        resp = await client.request(
            "DELETE", f"{BASE}/{acc.id}/vms/{vm.id}?deprovision=true",
            headers=_hdr(admin_role_token_a),
        )
        assert resp.status_code == 200, resp.text
        deprov = [c for c in calls if c["task_kind"] == "vm.account_deprovision"]
        assert len(deprov) == 1
        assert deprov[0]["payload"]["vm_id"] == vm.id
        assert deprov[0]["payload"]["login"] == "poolu"

    async def test_detach_unknown_vm_404(
        self, client, admin_role_token_a, make_hub, make_vm, make_pool_account,
    ):
        hub = await make_hub()
        vm = await make_vm(hub=hub)
        acc = await make_pool_account()  # не привязан к vm
        resp = await client.request(
            "DELETE", f"{BASE}/{acc.id}/vms/{vm.id}",
            headers=_hdr(admin_role_token_a),
        )
        assert_error(resp, 404, "ACCOUNT_VM_LINK_NOT_FOUND")

    async def test_reader_cannot_attach(
        self, client, reader_token_a, make_hub, make_vm, make_pool_account,
    ):
        hub = await make_hub()
        vm = await make_vm(hub=hub)
        acc = await make_pool_account()
        resp = await client.post(
            f"{BASE}/{acc.id}/vms", json={"vm_ids": [vm.id]},
            headers=_hdr(reader_token_a),
        )
        assert resp.status_code == 403


# ── provision / update / deprovision в госте ────────────────────────────────


class TestGuestDispatch:
    async def test_provision_dispatch(
        self, client, operator_token_a, make_hub, make_vm, make_pool_account, monkeypatch,
    ):
        calls = make_dispatch_capture(monkeypatch)
        hub = await make_hub()
        vm = await make_vm(hub=hub, ip_address="10.41.0.99")
        acc = await make_pool_account(vm=vm, login="app", unix_groups=["docker"])
        resp = await client.post(
            f"{BASE}/{acc.id}/vms/{vm.id}/provision",
            headers=_hdr(operator_token_a),
        )
        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["operation"] == "provision"
        assert body["vm_id"] == vm.id
        assert body["task_id"].startswith("tsk_")
        assert len(calls) == 1
        assert calls[0]["task_kind"] == "vm.account_provision"
        assert calls[0]["target_server_id"] == hub.id
        assert calls[0]["payload"]["unix_groups"] == ["docker"]
        assert calls[0]["payload"]["guest_ip"] == "10.41.0.99"

    async def test_update_on_host_dispatch(
        self, client, operator_token_a, make_hub, make_vm, make_pool_account, monkeypatch,
    ):
        calls = make_dispatch_capture(monkeypatch)
        hub = await make_hub()
        vm = await make_vm(hub=hub)
        acc = await make_pool_account(vm=vm)
        resp = await client.post(
            f"{BASE}/{acc.id}/vms/{vm.id}/update_on_host",
            headers=_hdr(operator_token_a),
        )
        assert resp.status_code == 202, resp.text
        assert resp.json()["operation"] == "update"
        assert calls[0]["task_kind"] == "vm.account_update_on_host"

    async def test_deprovision_dispatch_removes_link(
        self, client, db, admin_role_token_a, make_hub, make_vm, make_pool_account, monkeypatch,
    ):
        calls = make_dispatch_capture(monkeypatch)
        hub = await make_hub()
        vm = await make_vm(hub=hub)
        acc = await make_pool_account(vm=vm)
        resp = await client.post(
            f"{BASE}/{acc.id}/vms/{vm.id}/deprovision?remove_home=true",
            headers=_hdr(admin_role_token_a),
        )
        assert resp.status_code == 202, resp.text
        assert resp.json()["operation"] == "deprovision"
        assert calls[0]["task_kind"] == "vm.account_deprovision"
        assert calls[0]["payload"]["remove_home"] is True
        # deprovision = полная отвязка от ВМ.
        assert await _vm_link_count(db, acc.id, vm.id) == 0

    async def test_provision_requires_linked_vm(
        self, client, operator_token_a, make_hub, make_vm, make_pool_account, monkeypatch,
    ):
        make_dispatch_capture(monkeypatch)
        hub = await make_hub()
        vm = await make_vm(hub=hub)
        acc = await make_pool_account()  # не привязан
        resp = await client.post(
            f"{BASE}/{acc.id}/vms/{vm.id}/provision",
            headers=_hdr(operator_token_a),
        )
        assert_error(resp, 404, "ACCOUNT_NOT_FOUND")

    async def test_reader_cannot_provision(
        self, client, reader_token_a, make_hub, make_vm, make_pool_account, monkeypatch,
    ):
        calls = make_dispatch_capture(monkeypatch)
        hub = await make_hub()
        vm = await make_vm(hub=hub)
        acc = await make_pool_account(vm=vm)
        resp = await client.post(
            f"{BASE}/{acc.id}/vms/{vm.id}/provision",
            headers=_hdr(reader_token_a),
        )
        assert resp.status_code == 403
        assert calls == []


# ── ротация: re-provision в гостях + reveal для ВМ-привязанной учётки ────────


class TestRotateAndReveal:
    async def test_rotate_reprovisions_vm_guests(
        self, client, admin_role_token_a, make_hub, make_vm, make_pool_account, monkeypatch,
    ):
        calls = make_dispatch_capture(monkeypatch)
        hub = await make_hub()
        vm = await make_vm(hub=hub)
        acc = await make_pool_account(vm=vm, present_on_vm=True)
        resp = await client.post(
            f"{BASE}/{acc.id}/rotate_password",
            headers=_hdr(admin_role_token_a),
        )
        assert resp.status_code == 200, resp.text
        prov = [c for c in calls if c["task_kind"] == "vm.account_provision"]
        assert len(prov) == 1
        assert prov[0]["payload"]["vm_id"] == vm.id

    async def test_reveal_works_for_vm_linked_account(
        self, client, admin_role_token_a, make_hub, make_vm, make_pool_account,
    ):
        import base64

        hub = await make_hub()
        vm = await make_vm(hub=hub)
        acc = await make_pool_account(vm=vm, password="secret-guest-pwd")
        # Учётка без единой серверной привязки — reveal завязан на саму учётку.
        resp = await client.get(f"{BASE}/{acc.id}", headers=_hdr(admin_role_token_a))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["password_b64"] is not None
        assert base64.b64decode(body["password_b64"]).decode() == "secret-guest-pwd"
