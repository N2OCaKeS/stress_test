"""Тесты VM-вкладок: учётки (`/vms/{id}/accounts`), пакеты гостя
(`/vms/{id}/packages` + callback `record_vm_packages`) и графическая консоль
spice/serial (`/vms/{id}/console`).

`worker_client.dispatch_task` мочится — тесты фиксируют argument-shape и
HTTP-контракт, реального Redis/worker-БД нет.
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio

from tests._helpers import assert_error, auth_hdr as _hdr, make_dispatch_capture

BASE = "/api/server/v1"


@pytest_asyncio.fixture
async def make_hub(db):
    """Сервер-hub в dep_a: is_managed + is_vms_hub + virtualization + ёмкость."""
    from src.models import Server, ServerDisk
    from src.utils.ids import _new_id, server_id as new_id

    async def _factory(*, department_id: str = "dep_a") -> Server:
        suffix = uuid.uuid4().hex[:6]
        srv = Server(
            id=new_id(),
            hostname=f"hub-{suffix}",
            ip_address=f"10.40.{int(suffix[:2], 16) % 256}.{int(suffix[2:4], 16) % 256}",
            ssh_port=22,
            department_id=department_id,
            is_managed=True,
            is_vms_hub=True,
            virtualization=True,
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
    """ВМ в БД напрямую (для accounts/packages/console-тестов)."""
    from src.models import Vm
    from src.utils.ids import vm_id as new_id

    async def _factory(
        *, hub, department_id: str = "dep_a", name: str | None = None,
        status: str = "free", is_managed: bool = False,
        ip_address: str | None = None, mgmt_user: str | None = None,
        graphics: str = "vnc", graphics_port: int | None = None,
    ) -> Vm:
        vm = Vm(
            id=new_id(),
            name=name or f"vm-{uuid.uuid4().hex[:6]}",
            hub_server_id=hub.id,
            department_id=department_id,
            status=status,
            cpu=4, ram_mb=8192, disk_gb=100,
            is_managed=is_managed,
            ip_address=ip_address,
            mgmt_user=mgmt_user,
            graphics=graphics,
            graphics_port=graphics_port,
        )
        db.add(vm)
        await db.flush()
        await db.refresh(vm)
        return vm

    return _factory


@pytest_asyncio.fixture
async def make_account_on_vm(db):
    """Учётка + привязка к ВМ (`server_account_vms`)."""
    from src.models import ServerAccount, ServerAccountVm
    from src.utils.ids import server_account_id, server_account_vm_id

    async def _factory(
        *, vm, login: str, department_id: str = "dep_a",
        has_sudo: bool = False, unix_groups: list[str] | None = None,
        ssh_public_key: str | None = None, present_on_vm: bool = True,
    ) -> ServerAccount:
        acc = ServerAccount(
            id=server_account_id(),
            department_id=department_id,
            login=login,
            has_sudo=has_sudo,
            unix_groups=unix_groups or [],
            ssh_public_key=ssh_public_key,
        )
        db.add(acc)
        await db.flush()
        db.add(ServerAccountVm(
            id=server_account_vm_id(),
            account_id=acc.id,
            vm_id=vm.id,
            login=login,
            present_on_vm=present_on_vm,
        ))
        await db.flush()
        return acc

    return _factory


# ── учётки ВМ ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_vm_accounts(client, admin_role_token_a, make_hub, make_vm, make_account_on_vm):
    hub = await make_hub()
    vm = await make_vm(hub=hub)
    await make_account_on_vm(
        vm=vm, login="root", has_sudo=True, unix_groups=["wheel", "docker"],
        ssh_public_key="ssh-ed25519 AAAA root@x", present_on_vm=True,
    )
    await make_account_on_vm(vm=vm, login="tester", present_on_vm=False)

    resp = await client.get(f"{BASE}/vms/{vm.id}/accounts", headers=_hdr(admin_role_token_a))
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert len(rows) == 2
    by_login = {r["login"]: r for r in rows}
    root = by_login["root"]
    assert root["has_sudo"] is True
    assert root["unix_groups"] == ["wheel", "docker"]
    assert root["ssh_public_key"] == "ssh-ed25519 AAAA root@x"
    assert root["present_on_vm"] is True
    assert root["account_id"].startswith("acc_")
    assert by_login["tester"]["present_on_vm"] is False
    # Секретов в ответе нет.
    assert "password" not in root
    assert "ssh_private_key" not in root


@pytest.mark.asyncio
async def test_list_vm_accounts_empty(client, admin_role_token_a, make_hub, make_vm):
    hub = await make_hub()
    vm = await make_vm(hub=hub)
    resp = await client.get(f"{BASE}/vms/{vm.id}/accounts", headers=_hdr(admin_role_token_a))
    assert resp.status_code == 200, resp.text
    assert resp.json() == []


@pytest.mark.asyncio
async def test_list_vm_accounts_cross_dept_404(client, admin_role_token_a, make_hub, make_vm):
    hub = await make_hub(department_id="dep_b")
    vm = await make_vm(hub=hub, department_id="dep_b")
    resp = await client.get(f"{BASE}/vms/{vm.id}/accounts", headers=_hdr(admin_role_token_a))
    assert_error(resp, 404, "VM_NOT_FOUND")


@pytest.mark.asyncio
async def test_list_vm_accounts_denied_without_view(client, make_token, make_hub, make_vm):
    hub = await make_hub()
    vm = await make_vm(hub=hub)
    # Токен без ролей server_service → нет (vm, view).
    no_role = make_token(department_id="dep_a", service_roles={}, username="nobody")
    resp = await client.get(f"{BASE}/vms/{vm.id}/accounts", headers=_hdr(no_role))
    assert resp.status_code == 403


# ── пакеты гостя ВМ ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_packages_empty_no_refresh(client, admin_role_token_a, make_hub, make_vm):
    hub = await make_hub()
    vm = await make_vm(hub=hub)
    resp = await client.get(f"{BASE}/vms/{vm.id}/packages", headers=_hdr(admin_role_token_a))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["packages"] == []
    assert body["package_count"] == 0
    assert body["dispatched"] is False
    assert body["task_id"] is None
    assert body["synced_at"] is None


@pytest.mark.asyncio
async def test_packages_refresh_dispatches(
    client, admin_role_token_a, make_hub, make_vm, monkeypatch,
):
    calls = make_dispatch_capture(monkeypatch)
    hub = await make_hub()
    vm = await make_vm(hub=hub, is_managed=True, ip_address="10.40.0.77")
    resp = await client.get(
        f"{BASE}/vms/{vm.id}/packages?refresh=true", headers=_hdr(admin_role_token_a),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["dispatched"] is True
    assert body["task_id"] is not None
    assert len(calls) == 1
    assert calls[0]["task_kind"] == "vm.list_packages"
    assert calls[0]["target_server_id"] == hub.id
    assert calls[0]["payload"]["host"] == str(hub.ip_address)
    assert calls[0]["payload"]["guest_ip"] == "10.40.0.77"
    assert calls[0]["payload"]["vm_id"] == vm.id


@pytest.mark.asyncio
async def test_packages_refresh_requires_prepared(
    client, admin_role_token_a, make_hub, make_vm, monkeypatch,
):
    make_dispatch_capture(monkeypatch)
    hub = await make_hub()
    vm = await make_vm(hub=hub, is_managed=False, ip_address="10.40.0.78")
    resp = await client.get(
        f"{BASE}/vms/{vm.id}/packages?refresh=true", headers=_hdr(admin_role_token_a),
    )
    assert_error(resp, 409, "VM_PREPARE_REQUIRED")


@pytest.mark.asyncio
async def test_packages_refresh_requires_guest_ip(
    client, admin_role_token_a, make_hub, make_vm, monkeypatch,
):
    make_dispatch_capture(monkeypatch)
    hub = await make_hub()
    vm = await make_vm(hub=hub, is_managed=True, ip_address=None)
    resp = await client.get(
        f"{BASE}/vms/{vm.id}/packages?refresh=true", headers=_hdr(admin_role_token_a),
    )
    assert_error(resp, 409, "VM_GUEST_IP_UNKNOWN")


@pytest.mark.asyncio
async def test_packages_callback_then_read(
    client, worker_bot_token_a, admin_role_token_a, make_hub, make_vm,
):
    hub = await make_hub()
    vm = await make_vm(hub=hub, is_managed=True, ip_address="10.40.0.79")
    # Воркер пишет инвентарь.
    resp = await client.post(
        f"{BASE}/internal/vms/{vm.id}/packages",
        json={
            "packages": [
                {"name": "bash", "version": "5.1-2"},
                {"name": "openssh-server", "version": "8.4p1"},
            ],
            "source": "dpkg",
            "task_id": "tsk_x",
        },
        headers=_hdr(worker_bot_token_a, dept="dep_a"),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["package_count"] == 2

    # Сохранённое видно оператору.
    resp = await client.get(f"{BASE}/vms/{vm.id}/packages", headers=_hdr(admin_role_token_a))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["package_count"] == 2
    assert body["source"] == "dpkg"
    assert body["synced_at"] is not None
    assert body["dispatched"] is False
    names = {p["name"] for p in body["packages"]}
    assert names == {"bash", "openssh-server"}


@pytest.mark.asyncio
async def test_packages_callback_overwrites(
    client, worker_bot_token_a, admin_role_token_a, make_hub, make_vm,
):
    hub = await make_hub()
    vm = await make_vm(hub=hub, is_managed=True, ip_address="10.40.0.80")
    for pkgs in (
        [{"name": "a", "version": "1"}, {"name": "b", "version": "2"}],
        [{"name": "c", "version": "3"}],
    ):
        resp = await client.post(
            f"{BASE}/internal/vms/{vm.id}/packages",
            json={"packages": pkgs, "source": "dpkg"},
            headers=_hdr(worker_bot_token_a, dept="dep_a"),
        )
        assert resp.status_code == 200, resp.text
    resp = await client.get(f"{BASE}/vms/{vm.id}/packages", headers=_hdr(admin_role_token_a))
    body = resp.json()
    assert body["package_count"] == 1
    assert body["packages"][0]["name"] == "c"


@pytest.mark.asyncio
async def test_packages_callback_requires_dept_header(
    client, worker_bot_token_a, make_hub, make_vm,
):
    hub = await make_hub()
    vm = await make_vm(hub=hub)
    resp = await client.post(
        f"{BASE}/internal/vms/{vm.id}/packages",
        json={"packages": []},
        headers=_hdr(worker_bot_token_a),  # без X-Target-Department-Id
    )
    assert_error(resp, 403, "TARGET_DEPARTMENT_HEADER_REQUIRED")


# ── графическая консоль: spice + serial ──────────────────────────────────────


@pytest.mark.asyncio
async def test_console_spice_signed_token(client, admin_role_token_a, make_hub, make_vm):
    from src.core.config import get_settings
    from src.services import console_token

    hub = await make_hub()
    vm = await make_vm(hub=hub, graphics="spice", graphics_port=5901)
    resp = await client.post(
        f"{BASE}/vms/{vm.id}/console", json={"kind": "spice"},
        headers=_hdr(admin_role_token_a),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["kind"] == "spice"
    assert body["host"] == str(hub.ip_address)
    assert body["ws_path"] == f"/vm-console/spice/{vm.id}"
    assert body["ws_url"].endswith(f"/vm-console/spice/{vm.id}")
    assert body["port"] == 5901
    # Токен подписан и валидируется общим секретом (референс для прокси).
    claims = console_token.verify(body["token"], get_settings().vm_console_token_secret)
    assert claims is not None
    assert claims["vm_id"] == vm.id
    assert claims["kind"] == "spice"
    assert claims["hub_ip"] == str(hub.ip_address)
    assert claims["port"] == 5901


@pytest.mark.asyncio
async def test_console_vnc_signed_token(client, admin_role_token_a, make_hub, make_vm):
    from src.core.config import get_settings
    from src.services import console_token

    hub = await make_hub()
    vm = await make_vm(hub=hub)  # graphics=vnc, port unknown
    resp = await client.post(
        f"{BASE}/vms/{vm.id}/console", json={"kind": "vnc"},
        headers=_hdr(admin_role_token_a),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ws_url"].endswith(f"/vm-console/vnc/{vm.id}")
    assert body["port"] is None
    claims = console_token.verify(body["token"], get_settings().vm_console_token_secret)
    assert claims is not None and claims["kind"] == "vnc"


@pytest.mark.asyncio
async def test_console_serial(client, admin_role_token_a, make_hub, make_vm):
    hub = await make_hub()
    vm = await make_vm(hub=hub)
    resp = await client.post(
        f"{BASE}/vms/{vm.id}/console", json={"kind": "serial"},
        headers=_hdr(admin_role_token_a),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["kind"] == "serial"
    assert body["host"] == str(hub.ip_address)
    assert body["token"].startswith("vmc_")
    assert body["ws_url"] is None


@pytest.mark.asyncio
async def test_console_spice_token_rejects_tampering(client, admin_role_token_a, make_hub, make_vm):
    from src.core.config import get_settings
    from src.services import console_token

    hub = await make_hub()
    vm = await make_vm(hub=hub, graphics="spice", graphics_port=5902)
    resp = await client.post(
        f"{BASE}/vms/{vm.id}/console", json={"kind": "spice"},
        headers=_hdr(admin_role_token_a),
    )
    token = resp.json()["token"]
    secret = get_settings().vm_console_token_secret
    # Подмена подписи ломает валидацию; неверный секрет — тоже.
    assert console_token.verify(token + "x", secret) is None
    assert console_token.verify(token, "wrong-secret") is None
