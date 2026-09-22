"""Тесты `POST /api/server/v1/vms/{id}/packages/action` — мутация пакетов гостя ВМ.

VM-аналог серверного `POST /servers/packages/bulk-action`, но per-VM.
`worker_client.dispatch_task` мочится — фиксируем dispatch-контракт (task_kind,
payload: operation/packages/guest_ip) и гейты (RBAC `(vm, vm_astra_update)`,
бронь/lifecycle-lock, валидация тела). Реального worker'а нет.
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

    async def _factory(*, department_id: str = "dep_a"):
        suffix = uuid.uuid4().hex[:6]
        srv = Server(
            id=new_id(),
            hostname=f"hub-{suffix}",
            ip_address=f"10.42.{int(suffix[:2], 16) % 256}.{int(suffix[2:4], 16) % 256}",
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
    ):
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


def _url(vm_id: str) -> str:
    return f"{BASE}/vms/{vm_id}/packages/action"


# ── dispatch happy path ──────────────────────────────────────────────────────


class TestActionDispatch:
    @pytest.mark.parametrize(
        "action,task_kind",
        [
            ("install", "vm.install_packages"),
            ("remove", "vm.remove_packages"),
            ("update", "vm.update_packages"),
        ],
    )
    @pytest.mark.asyncio
    async def test_dispatches_action(
        self, client, admin_role_token_a, make_hub, make_vm, monkeypatch,
        action, task_kind,
    ):
        calls = make_dispatch_capture(monkeypatch)
        hub = await make_hub()
        vm = await make_vm(hub=hub, is_managed=True, ip_address="10.42.0.77")

        resp = await client.post(
            _url(vm.id), headers=_hdr(admin_role_token_a),
            json={"action": action, "packages": ["sl", "cowsay"]},
        )
        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["vm_id"] == vm.id
        assert body["task_id"] is not None
        assert len(calls) == 1
        assert calls[0]["task_kind"] == task_kind
        assert calls[0]["target_server_id"] == hub.id
        assert calls[0]["target_resource_id"] == vm.id
        assert calls[0]["payload"]["operation"] == action
        assert calls[0]["payload"]["packages"] == ["sl", "cowsay"]
        assert calls[0]["payload"]["guest_ip"] == "10.42.0.77"
        assert calls[0]["payload"]["host"] == str(hub.ip_address)

    @pytest.mark.asyncio
    async def test_update_without_packages_updates_all(
        self, client, admin_role_token_a, make_hub, make_vm, monkeypatch,
    ):
        calls = make_dispatch_capture(monkeypatch)
        hub = await make_hub()
        vm = await make_vm(hub=hub)
        resp = await client.post(
            _url(vm.id), headers=_hdr(admin_role_token_a),
            json={"action": "update"},
        )
        assert resp.status_code == 202, resp.text
        assert calls[0]["payload"]["operation"] == "update"
        assert calls[0]["payload"]["packages"] == []

    @pytest.mark.asyncio
    async def test_unmanaged_vm_dispatches_without_guest_ip_hint(
        self, client, admin_role_token_a, make_hub, make_vm, monkeypatch,
    ):
        """Не-managed ВМ без IP: dispatch проходит (worker сам резолвит IP)."""
        calls = make_dispatch_capture(monkeypatch)
        hub = await make_hub()
        vm = await make_vm(hub=hub, is_managed=False, ip_address=None)
        resp = await client.post(
            _url(vm.id), headers=_hdr(admin_role_token_a),
            json={"action": "install", "packages": ["sl"]},
        )
        assert resp.status_code == 202, resp.text
        assert "guest_ip" not in calls[0]["payload"]


# ── гейты брони / lifecycle-lock ─────────────────────────────────────────────


class TestGates:
    @pytest.mark.asyncio
    async def test_busy_vm_returns_409(
        self, client, admin_role_token_a, make_hub, make_vm, monkeypatch, db,
    ):
        calls = make_dispatch_capture(monkeypatch)
        hub = await make_hub()
        vm = await make_vm(hub=hub)
        vm.busy_state = "updating"
        await db.flush()
        resp = await client.post(
            _url(vm.id), headers=_hdr(admin_role_token_a),
            json={"action": "install", "packages": ["sl"]},
        )
        assert_error(resp, 409, "VM_BUSY")
        assert len(calls) == 0

    @pytest.mark.asyncio
    async def test_cross_dept_404(
        self, client, admin_role_token_a, make_hub, make_vm, monkeypatch,
    ):
        make_dispatch_capture(monkeypatch)
        hub = await make_hub(department_id="dep_b")
        vm = await make_vm(hub=hub, department_id="dep_b")
        resp = await client.post(
            _url(vm.id), headers=_hdr(admin_role_token_a),
            json={"action": "install", "packages": ["sl"]},
        )
        assert_error(resp, 404, "VM_NOT_FOUND")


# ── RBAC ─────────────────────────────────────────────────────────────────────


class TestRbac:
    @pytest.mark.asyncio
    async def test_guest_without_astra_update_403(
        self, client, guest_token_a, make_hub, make_vm, monkeypatch,
    ):
        calls = make_dispatch_capture(monkeypatch)
        hub = await make_hub()
        vm = await make_vm(hub=hub)
        resp = await client.post(
            _url(vm.id), headers=_hdr(guest_token_a),
            json={"action": "install", "packages": ["sl"]},
        )
        assert resp.status_code == 403, resp.text
        assert len(calls) == 0


# ── валидация тела ───────────────────────────────────────────────────────────


class TestValidation:
    @pytest.mark.parametrize("bad", [
        "sl; rm -rf /", "$(reboot)", "`id`", "foo bar", "foo|cat", "*", "-malformed",
    ])
    @pytest.mark.asyncio
    async def test_invalid_package_name_422(
        self, client, admin_role_token_a, make_hub, make_vm, monkeypatch, bad,
    ):
        calls = make_dispatch_capture(monkeypatch)
        hub = await make_hub()
        vm = await make_vm(hub=hub)
        resp = await client.post(
            _url(vm.id), headers=_hdr(admin_role_token_a),
            json={"action": "install", "packages": [bad]},
        )
        assert resp.status_code == 422, resp.text
        assert len(calls) == 0

    @pytest.mark.asyncio
    async def test_empty_packages_install_422(
        self, client, admin_role_token_a, make_hub, make_vm, monkeypatch,
    ):
        make_dispatch_capture(monkeypatch)
        hub = await make_hub()
        vm = await make_vm(hub=hub)
        resp = await client.post(
            _url(vm.id), headers=_hdr(admin_role_token_a),
            json={"action": "install", "packages": []},
        )
        assert resp.status_code == 422, resp.text

    @pytest.mark.asyncio
    async def test_unknown_action_422(
        self, client, admin_role_token_a, make_hub, make_vm, monkeypatch,
    ):
        make_dispatch_capture(monkeypatch)
        hub = await make_hub()
        vm = await make_vm(hub=hub)
        resp = await client.post(
            _url(vm.id), headers=_hdr(admin_role_token_a),
            json={"action": "purge", "packages": ["sl"]},
        )
        assert resp.status_code == 422, resp.text

    @pytest.mark.asyncio
    async def test_legit_names_accepted(
        self, client, admin_role_token_a, make_hub, make_vm, monkeypatch,
    ):
        calls = make_dispatch_capture(monkeypatch)
        hub = await make_hub()
        vm = await make_vm(hub=hub)
        resp = await client.post(
            _url(vm.id), headers=_hdr(admin_role_token_a),
            json={"action": "install",
                  "packages": ["linux-image-amd64", "g++", "python3.11", "lib32z1"]},
        )
        assert resp.status_code == 202, resp.text
        assert calls[0]["payload"]["packages"] == [
            "linux-image-amd64", "g++", "python3.11", "lib32z1",
        ]
