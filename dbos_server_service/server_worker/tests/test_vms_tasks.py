"""Тесты worker-тасок VM-менеджера: `vms_hub.prepare` / `vm.create` / `vm.power`.

SSH мокается `_FakeSshClient` (дефолт rc=0, точечные ответы по подстроке
команды) — как в `test_astra_update_task`. Ассертим последовательности
`ssh.run`, снимки (plain vs `_build`) и internal-callback'и в server_service.
"""

from __future__ import annotations

import pytest
from sqlalchemy import update

from src.clients.ssh import SshError
from src.core.constants import TaskStatus
from src.db.session import AsyncSessionLocal
from src.models import Task
from src.tasks import _vms_helpers, vms


# ── SSH mock ─────────────────────────────────────────────────────────────────


class _FakeSshClient:
    """Мок SshClient: пишет (command, stdin, sudo), отдаёт заданный ответ по
    подстроке команды. Дефолт — (0, "", "") (команда «прошла»)."""

    def __init__(self, host: str = "10.0.0.7"):
        self.host = host
        self._responses: list[tuple[str, tuple[int, str, str]]] = []
        self.commands: list[str] = []
        self.stdins: list[str | None] = []

    def set_response(self, pat: str, rc: int, stdout: str = "", stderr: str = ""):
        self._responses.append((pat, (rc, stdout, stderr)))

    async def connect(self):
        return None

    async def close(self):
        return None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return None

    async def run(self, command, *, sudo=False, stdin_payload=None):  # noqa: ARG002
        self.commands.append(command)
        self.stdins.append(stdin_payload)
        for pat, resp in self._responses:
            if pat in command:
                return resp
        return (0, "", "")


@pytest.fixture(autouse=True)
def stub_session_and_callbacks(monkeypatch):
    """Замокать open_hub_session (отдаёт готовый fake) и оба VM-callback'а.

    Тест кладёт свой fake в `holder['ssh']` до запуска таски. Возвращает dict со
    списками вызовов callback'ов.
    """
    holder: dict = {"ssh": None}
    calls: dict = {"vm_state": [], "hub_state": []}

    async def _open(payload):  # noqa: ARG001
        fake = holder["ssh"]
        return fake, fake.host

    monkeypatch.setattr(vms, "open_hub_session", _open)

    async def _vm_state(vm_id, target_department_id=None, **kw):
        calls["vm_state"].append({"vm_id": vm_id, "target_department_id": target_department_id, **kw})
        return {"ok": True}

    async def _hub_state(server_id, prepared, target_department_id=None, **kw):
        calls["hub_state"].append({"server_id": server_id, "prepared": prepared, "target_department_id": target_department_id, **kw})
        return {"ok": True}

    monkeypatch.setattr(vms.server_service_client, "submit_vm_state", _vm_state)
    monkeypatch.setattr(vms.server_service_client, "submit_vms_hub_state", _hub_state)
    return {"holder": holder, "calls": calls}


async def _set_single_attempt(tid: str):
    async with AsyncSessionLocal() as session:
        await session.execute(update(Task).where(Task.id == tid).values(max_attempts=1))
        await session.commit()


# ── чистые хелперы ───────────────────────────────────────────────────────────


class TestHelpers:
    def test_map_domstate(self):
        assert _vms_helpers.map_domstate("running") == "on"
        assert _vms_helpers.map_domstate("shut off") == "off"
        assert _vms_helpers.map_domstate("paused") == "paused"
        assert _vms_helpers.map_domstate("in shutdown") == "shutting_down"
        assert _vms_helpers.map_domstate("???") == "unknown"

    def test_parse_domifaddr(self):
        out = (
            " Name       MAC                Protocol     Address\n"
            "-------------------------------------------------------\n"
            " vnet0      52:54:00:aa:bb:cc  ipv4         192.168.100.24/24\n"
        )
        assert _vms_helpers.parse_domifaddr(out) == "192.168.100.24"
        assert _vms_helpers.parse_domifaddr("no address here") is None

    def test_validators_reject_metachars(self):
        for bad in ("a;b", "a b", "a$(x)", "a`b`", "a'b"):
            with pytest.raises(SshError) as e:
                _vms_helpers.validate_name(bad, "h")
            assert e.value.error_code == "VM_INVALID_ARG"
        assert _vms_helpers.validate_name("1.7.5.9_build", "h") == "1.7.5.9_build"

    def test_validate_ip_and_path(self):
        assert _vms_helpers.validate_ip("10.177.103.42", "h") == "10.177.103.42"
        assert _vms_helpers.validate_ip("10.177.103.42/24", "h") == "10.177.103.42/24"
        with pytest.raises(SshError):
            _vms_helpers.validate_path("relative/path", "h")

    def test_image_url(self):
        assert vms._image_url("vm_station") == (
            "ftp://10.177.103.10/boxes/vm_station.tar.gz", "vm_station",
        )
        assert vms._image_url("ftp://x/boxes/foo.tar.gz") == (
            "ftp://x/boxes/foo.tar.gz", "foo",
        )


# ── vms_hub.prepare ──────────────────────────────────────────────────────────


def _prepare_payload(os_family="apt"):
    return {
        "server_id": "hub1",
        "host": "10.0.0.7",
        "phy_if": "ens192",
        "os_family": os_family,
        "storage_pool_path": "/vms",
        "image_refs": ["vm_station"],
        "is_managed": True,
        "management_user": "dbos",
        "target_department_id": "dep1",
    }


def _prepare_fake():
    fake = _FakeSshClient()
    fake.set_response("test -e /dev/kvm", 0)
    fake.set_response("ip link show br0", 1)  # моста нет → настраиваем
    fake.set_response(
        "ip -o -4 addr show dev ens192", 0,
        "2: ens192    inet 10.177.103.207/24 brd 10.177.103.255 scope global ens192",
    )
    fake.set_response("ip route show default", 0, "default via 10.177.103.254 dev ens192")
    fake.set_response("^nameserver", 0, "nameserver 10.177.180.246")
    fake.set_response("virsh pool-info", 1)  # пула нет → создаём
    fake.set_response("test -f", 1)  # образа нет → качаем
    return fake


class TestVmsHubPrepare:
    async def test_apt_family_full_flow(self, make_task, fetch_task, captured_audit, stub_session_and_callbacks):
        fake = _prepare_fake()
        stub_session_and_callbacks["holder"]["ssh"] = fake
        tid = await make_task(task_kind="vms_hub.prepare", target_server_id="hub1", payload=_prepare_payload("apt"))
        await vms.vms_hub_prepare.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        cmds = fake.commands
        assert any("apt-get install -y astra-kvm virtinst qemu-utils" in c for c in cmds)
        # libguestfs-tools (virt-customize для offline-фолбэка статики)
        assert any("apt-get install -y" in c and "libguestfs-tools" in c for c in cmds)
        assert any("usermod -aG" in c and "dbos" in c for c in cmds)
        assert any("qemu.conf" in c for c in cmds)
        assert any("systemctl enable --now libvirtd" in c for c in cmds)
        # мост: bridge_ports уходит в stdin tee-конфига
        tee_idx = next(i for i, c in enumerate(cmds) if "interfaces.d/dbos-br0.cfg" in c)
        assert "bridge_ports ens192" in (fake.stdins[tee_idx] or "")
        assert "address 10.177.103.207/24" in (fake.stdins[tee_idx] or "")
        assert any("DOCKER-USER" in c for c in cmds)
        assert any("bridge-nf-call-iptables=0" in c for c in cmds)
        assert any("pool-define-as vms dir --target /vms" in c for c in cmds)
        assert any("wget" in c and "vm_station.tar.gz" in c for c in cmds)
        # callback prepared=True + phy_if
        assert stub_session_and_callbacks["calls"]["hub_state"] == [
            {"server_id": "hub1", "prepared": True, "target_department_id": "dep1", "phy_if": "ens192"},
        ]

    async def test_dnf_family_packages(self, make_task, fetch_task, captured_audit, stub_session_and_callbacks):
        fake = _prepare_fake()
        stub_session_and_callbacks["holder"]["ssh"] = fake
        tid = await make_task(task_kind="vms_hub.prepare", target_server_id="hub1", payload=_prepare_payload("dnf"))
        await vms.vms_hub_prepare.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        assert any("dnf install -y qemu-kvm libvirt virt-install" in c for c in fake.commands)
        assert any("dnf install -y" in c and "libguestfs-tools" in c for c in fake.commands)
        assert not any("apt-get install" in c for c in fake.commands)
        # dnf-мост через nmcli
        assert any("nmcli con add type bridge ifname br0" in c for c in fake.commands)

    async def test_no_kvm_fails_before_packages(self, make_task, fetch_task, captured_audit, stub_session_and_callbacks):
        fake = _FakeSshClient()
        fake.set_response("test -e /dev/kvm", 1)  # нет kvm
        stub_session_and_callbacks["holder"]["ssh"] = fake
        tid = await make_task(task_kind="vms_hub.prepare", target_server_id="hub1", payload=_prepare_payload("apt"))
        await _set_single_attempt(tid)
        await vms.vms_hub_prepare.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.FAILED
        assert t.last_error and "VMS_HUB_NO_KVM" in t.last_error
        # ничего кроме precheck не выполнялось
        assert not any("apt-get install" in c for c in fake.commands)
        assert not any("virt" in c for c in fake.commands)
        # failed-callback prepared=False
        assert stub_session_and_callbacks["calls"]["hub_state"][0]["prepared"] is False


# ── vm.create ────────────────────────────────────────────────────────────────


def _create_fake():
    fake = _FakeSshClient()
    fake.set_response("test -f", 0)  # бокс в пуле
    fake.set_response("virsh domifaddr", 0, " vnet0 52:54:00:aa:bb:cc ipv4 192.168.100.24/24")
    fake.set_response("virsh domstate", 0, "running")
    return fake


class TestVmCreateUniversal:
    async def test_universal_builds_build_and_plain_snapshots(self, make_task, fetch_task, captured_audit, stub_session_and_callbacks):
        fake = _create_fake()
        stub_session_and_callbacks["holder"]["ssh"] = fake
        payload = {
            "vm_id": "vm1", "hub_host": "10.0.0.7", "name": "station-a",
            "cpu": 16, "ram_mb": 131072, "disk_gb": 0, "box": "vm_station",
            "network_mode": "bridge", "ip_address": "10.177.103.101",
            "os_versions": ["1.7.5.9"], "storage_pool_path": "/vms",
            "is_managed": True, "target_department_id": "dep1",
        }
        tid = await make_task(task_kind="vm.create", target_server_id="hub1", payload=payload)
        await vms.vm_create.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        cmds = fake.commands
        assert any("cp /vms/vm_station.qcow2 /vms/station-a.qcow2" in c for c in cmds)
        assert any("virt-install -n station-a" in c and "host-model,+vmx" in c for c in cmds)
        assert any("qemu-img snapshot -a 1.7.5.9 /vms/station-a.qcow2" in c for c in cmds)
        assert any("snapshot-create-as station-a --name 1.7.5.9_build" in c for c in cmds)
        assert any("snapshot-create-as station-a --name 1.7.5.9 " in c for c in cmds)
        assert any("virt-xml station-a --edit --network bridge=br0" in c for c in cmds)
        # callback: только plain-снимок, без _build
        state = stub_session_and_callbacks["calls"]["vm_state"][0]
        assert state["snapshots"] == ["1.7.5.9"]
        assert state["power_state"] == "on"
        assert state["status"] == "free"
        assert state["ip_address"] == "10.177.103.101"

    async def test_universal_requires_versions(self, make_task, fetch_task, captured_audit, stub_session_and_callbacks):
        fake = _create_fake()
        stub_session_and_callbacks["holder"]["ssh"] = fake
        payload = {
            "vm_id": "vm1", "hub_host": "10.0.0.7", "name": "station-a",
            "cpu": 4, "ram_mb": 4096, "box": "vm_station",
            "network_mode": "bridge", "ip_address": "10.177.103.101",
            "os_versions": [], "is_managed": True,
        }
        tid = await make_task(task_kind="vm.create", target_server_id="hub1", payload=payload)
        await _set_single_attempt(tid)
        await vms.vm_create.original_func(tid)
        t = await fetch_task(tid)
        assert t.status == TaskStatus.FAILED
        assert t.last_error and "VM_INVALID_ARG" in t.last_error
        assert stub_session_and_callbacks["calls"]["vm_state"][0]["status"] == "error"


class TestVmCreateSingle:
    async def test_single_box_build_snapshot(self, make_task, fetch_task, captured_audit, stub_session_and_callbacks):
        fake = _create_fake()
        stub_session_and_callbacks["holder"]["ssh"] = fake
        payload = {
            "vm_id": "vm2", "hub_host": "10.0.0.7", "name": "xfs-1",
            "cpu": 8, "ram_mb": 8192, "disk_gb": 15, "box": "xfs.box",
            "network_mode": "nat", "ip_address": None, "os_versions": [],
            "storage_pool_path": "/vms", "is_managed": True,
        }
        tid = await make_task(task_kind="vm.create", target_server_id="hub1", payload=payload)
        await vms.vm_create.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        cmds = fake.commands
        assert any("qemu-img resize /vms/xfs-1.qcow2 15G" in c for c in cmds)
        assert any("network=test" in c for c in cmds)  # nat
        assert any("snapshot-create-as xfs-1 --name build" in c for c in cmds)
        state = stub_session_and_callbacks["calls"]["vm_state"][0]
        assert state["snapshots"] == ["build"]


# ── vm.power ─────────────────────────────────────────────────────────────────


class TestVmPower:
    @pytest.mark.parametrize("action,verb,domstate,expected", [
        ("start", "virsh start", "running", "on"),
        ("shutdown", "virsh shutdown", "shut off", "off"),
        ("reboot", "virsh reboot", "running", "on"),
        ("reset", "virsh reset", "running", "on"),
        ("destroy", "virsh destroy", "shut off", "off"),
    ])
    async def test_power_actions(self, action, verb, domstate, expected, make_task, fetch_task, captured_audit, stub_session_and_callbacks):
        fake = _FakeSshClient()
        fake.set_response("virsh domstate", 0, domstate)
        stub_session_and_callbacks["holder"]["ssh"] = fake
        payload = {
            "vm_id": "vm9", "hub_host": "10.0.0.7", "vm_name": "station-a",
            "action": action, "is_managed": True, "target_department_id": "dep1",
        }
        tid = await make_task(task_kind="vm.power", target_server_id="hub1", payload=payload)
        await vms.vm_power.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        assert any(f"{verb} station-a" in c for c in fake.commands)
        assert t.result["power_state"] == expected
        state = stub_session_and_callbacks["calls"]["vm_state"][0]
        assert state == {"vm_id": "vm9", "target_department_id": "dep1", "power_state": expected}

    async def test_invalid_action_rejected(self, make_task, fetch_task, captured_audit, stub_session_and_callbacks):
        fake = _FakeSshClient()
        stub_session_and_callbacks["holder"]["ssh"] = fake
        payload = {"vm_id": "vm9", "hub_host": "10.0.0.7", "vm_name": "s", "action": "melt", "is_managed": True}
        tid = await make_task(task_kind="vm.power", target_server_id="hub1", payload=payload)
        await _set_single_attempt(tid)
        await vms.vm_power.original_func(tid)
        t = await fetch_task(tid)
        assert t.status == TaskStatus.FAILED
        assert t.last_error and "VM_INVALID_ARG" in t.last_error


# ── vm.delete ────────────────────────────────────────────────────────────────


class TestVmDelete:
    async def test_destroy_then_undefine(self, make_task, fetch_task, captured_audit, stub_session_and_callbacks):
        fake = _FakeSshClient()
        stub_session_and_callbacks["holder"]["ssh"] = fake
        payload = {
            "vm_id": "vm9", "hub_host": "10.0.0.7", "vm_name": "station-a",
            "is_managed": True, "target_department_id": "dep1",
        }
        tid = await make_task(task_kind="vm.delete", target_server_id="hub1", payload=payload)
        await vms.vm_delete.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        cmds = fake.commands
        di = next(i for i, c in enumerate(cmds) if "virsh destroy station-a" in c)
        ui = next(i for i, c in enumerate(cmds) if "virsh undefine station-a --remove-all-storage --snapshots-metadata" in c)
        assert di < ui
        assert t.result == {"vm_id": "vm9", "vm_name": "station-a", "destroyed": True, "undefined": True}
        # карточка ВМ снесена в БД до dispatch'а — state-callback'а нет.
        assert stub_session_and_callbacks["calls"]["vm_state"] == []

    async def test_destroy_nonzero_tolerated(self, make_task, fetch_task, captured_audit, stub_session_and_callbacks):
        # destroy на уже выключенной ВМ отдаёт non-zero, undefine — код 1: обе ок.
        fake = _FakeSshClient()
        fake.set_response("virsh destroy", 1, stderr="domain is not running")
        fake.set_response("virsh undefine", 1, stderr="already gone")
        stub_session_and_callbacks["holder"]["ssh"] = fake
        payload = {
            "vm_id": "vm9", "hub_host": "10.0.0.7", "vm_name": "station-a", "is_managed": True,
        }
        tid = await make_task(task_kind="vm.delete", target_server_id="hub1", payload=payload)
        await vms.vm_delete.original_func(tid)
        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        assert t.result["undefined"] is True

    async def test_undefine_failure_reports_error(self, make_task, fetch_task, captured_audit, stub_session_and_callbacks):
        fake = _FakeSshClient()
        fake.set_response("virsh undefine", 2, stderr="in use")
        stub_session_and_callbacks["holder"]["ssh"] = fake
        payload = {
            "vm_id": "vm9", "hub_host": "10.0.0.7", "vm_name": "station-a", "is_managed": True,
        }
        tid = await make_task(task_kind="vm.delete", target_server_id="hub1", payload=payload)
        await _set_single_attempt(tid)
        await vms.vm_delete.original_func(tid)
        t = await fetch_task(tid)
        assert t.status == TaskStatus.FAILED
        assert t.last_error and "VM_DELETE_FAILED" in t.last_error
