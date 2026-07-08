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
    calls: dict = {"vm_state": [], "hub_state": [], "snapshots": []}

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

    async def _snapshots(vm_id, snapshots, target_department_id=None, **kw):
        calls["snapshots"].append({"vm_id": vm_id, "snapshots": snapshots, "target_department_id": target_department_id, **kw})
        return {"ok": True}

    monkeypatch.setattr(vms.server_service_client, "submit_vm_state", _vm_state)
    monkeypatch.setattr(vms.server_service_client, "submit_vms_hub_state", _hub_state)
    monkeypatch.setattr(vms.server_service_client, "submit_vm_snapshots", _snapshots)
    # Смена режима гостя ребутит его — реальное ожидание минуты, в тестах 0.
    monkeypatch.setattr(_vms_helpers, "GUEST_MODE_REBOOT_SETTLE_S", 0)
    monkeypatch.setattr(_vms_helpers, "GUEST_MODE_REBOOT_POLL_DELAY_S", 0)
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


def _prepare_fake_bridge_present():
    """Как `_prepare_fake`, но br0 уже есть — ребут и guard не нужны."""
    fake = _FakeSshClient()
    fake.set_response("test -e /dev/kvm", 0)
    fake.set_response("ip link show br0", 0)  # мост уже поднят
    fake.set_response("virsh pool-info", 1)
    fake.set_response("test -f", 1)
    return fake


class _RebootDropSsh(_FakeSshClient):
    """Мок, который на reboot-триггере рвёт SSH (SshError) — как настоящий хост,
    успевший уйти в перезагрузку до закрытия сессии."""

    async def run(self, command, *, sudo=False, stdin_payload=None):  # noqa: ARG002
        self.commands.append(command)
        self.stdins.append(stdin_payload)
        if "systemctl reboot" in command:
            raise SshError(error_code="SSH_RUN_FAILED", host=self.host, message="dropped")
        for pat, resp in self._responses:
            if pat in command:
                return resp
        return (0, "", "")


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

    async def test_setup_bridge_returns_true_for_new_bridge(self):
        fake = _FakeSshClient()
        fake.set_response("ip link show br0", 1)  # моста нет
        fake.set_response("ip -o -4 addr show dev ens192", 0, "inet 10.177.103.207/24")
        fake.set_response("ip route show default", 0, "default via 10.177.103.254 dev ens192")
        fake.set_response("^nameserver", 0, "nameserver 10.177.180.246")
        result = await vms._setup_bridge(fake, "10.0.0.7", "ens192", "apt")
        assert result is True

    async def test_setup_bridge_returns_false_when_present(self):
        fake = _FakeSshClient()
        fake.set_response("ip link show br0", 0)  # мост уже есть
        result = await vms._setup_bridge(fake, "10.0.0.7", "ens192", "apt")
        assert result is False
        # адресацию не трогали
        assert not any("dbos-br0.cfg" in c for c in fake.commands)

    async def test_needs_reboot_installs_guard_then_reboots(
        self, make_task, fetch_task, captured_audit, stub_session_and_callbacks, monkeypatch,
    ):
        fake = _prepare_fake()  # br0 отсутствует → needs_reboot
        stub_session_and_callbacks["holder"]["ssh"] = fake
        # Переопределяем hub-callback, чтобы зафиксировать момент отправки
        # относительно уже выполненных SSH-команд.
        order: list[int] = []

        async def _hub_state(server_id, prepared, target_department_id=None, **kw):  # noqa: ARG001
            order.append(len(fake.commands))
            return {"ok": True}

        monkeypatch.setattr(vms.server_service_client, "submit_vms_hub_state", _hub_state)

        tid = await make_task(task_kind="vms_hub.prepare", target_server_id="hub1", payload=_prepare_payload("apt"))
        await vms.vms_hub_prepare.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        cmds = fake.commands
        # guard: скрипт + unit + enable
        guard_idx = next(i for i, c in enumerate(cmds) if "dbos-net-guard.sh" in c and "tee" in c)
        assert any("dbos-net-guard.service" in c and "tee" in c for c in cmds)
        assert any("systemctl enable dbos-net-guard.service" in c for c in cmds)
        # тело скрипта несёт шлюз, откат из бэкапа и финальный reboot
        script = fake.stdins[guard_idx] or ""
        assert "ping -c3 -W3 10.177.103.254" in script
        assert "interfaces.dbos-bak" in script
        assert "/sbin/reboot" in script
        # reboot-триггер — последняя команда таски
        reboot_idx = next(i for i, c in enumerate(cmds) if "systemctl reboot" in c)
        assert reboot_idx == len(cmds) - 1
        # callback ушёл ДО reboot-триггера
        assert order and order[0] <= reboot_idx

    async def test_reboot_trigger_drop_does_not_fail_task(
        self, make_task, fetch_task, captured_audit, stub_session_and_callbacks,
    ):
        fake = _RebootDropSsh()
        fake.set_response("test -e /dev/kvm", 0)
        fake.set_response("ip link show br0", 1)
        fake.set_response(
            "ip -o -4 addr show dev ens192", 0, "inet 10.177.103.207/24",
        )
        fake.set_response("ip route show default", 0, "default via 10.177.103.254 dev ens192")
        fake.set_response("^nameserver", 0, "nameserver 10.177.180.246")
        fake.set_response("virsh pool-info", 1)
        fake.set_response("test -f", 1)
        stub_session_and_callbacks["holder"]["ssh"] = fake
        tid = await make_task(task_kind="vms_hub.prepare", target_server_id="hub1", payload=_prepare_payload("apt"))
        await vms.vms_hub_prepare.original_func(tid)

        t = await fetch_task(tid)
        # обрыв SSH на reboot-триггере не роняет таску
        assert t.status == TaskStatus.SUCCEEDED
        assert stub_session_and_callbacks["calls"]["hub_state"] == [
            {"server_id": "hub1", "prepared": True, "target_department_id": "dep1", "phy_if": "ens192"},
        ]

    async def test_no_reboot_when_bridge_already_present(
        self, make_task, fetch_task, captured_audit, stub_session_and_callbacks,
    ):
        fake = _prepare_fake_bridge_present()
        stub_session_and_callbacks["holder"]["ssh"] = fake
        tid = await make_task(task_kind="vms_hub.prepare", target_server_id="hub1", payload=_prepare_payload("apt"))
        await vms.vms_hub_prepare.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        cmds = fake.commands
        assert not any("dbos-net-guard" in c for c in cmds)
        assert not any("systemctl reboot" in c for c in cmds)
        # обычный callback prepared=True
        assert stub_session_and_callbacks["calls"]["hub_state"] == [
            {"server_id": "hub1", "prepared": True, "target_department_id": "dep1", "phy_if": "ens192"},
        ]

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
    async def test_universal_builds_golden_orel_smolensk(self, make_task, fetch_task, captured_audit, stub_session_and_callbacks):
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
        # disk_gb=0 → обычный cp (без virt-resize)
        assert any("cp /vms/vm_station.qcow2 /vms/station-a.qcow2" in c for c in cmds)
        assert not any("virt-resize" in c for c in cmds)
        assert any("virt-install -n station-a" in c and "--cpu host-model" in c for c in cmds)
        assert any("qemu-img snapshot -a 1.7.5.9 /vms/station-a.qcow2" in c for c in cmds)
        # golden (скрытый) + Орёл + Смоленск
        assert any("snapshot-create-as station-a --name 1.7.5.9_orel_build" in c for c in cmds)
        assert any("snapshot-create-as station-a --name 1.7.5.9_oryol" in c for c in cmds)
        assert any("snapshot-create-as station-a --name 1.7.5.9_smolensk" in c for c in cmds)
        assert any("virt-xml station-a --edit --network bridge=br0" in c for c in cmds)
        # смена режима на Смоленск: modeswitch + МРД + МКЦ (и порядок до снимка)
        assert any("astra-modeswitch set 2" in c for c in cmds)
        assert any("astra-mac-control enable" in c for c in cmds)
        assert any("astra-mic-control enable" in c for c in cmds)
        i_switch = next(i for i, c in enumerate(cmds) if "astra-modeswitch set 2" in c)
        i_smol = next(i for i, c in enumerate(cmds) if "--name 1.7.5.9_smolensk" in c)
        assert i_switch < i_smol
        # rich snapshot-callback: golden(is_system) + oryol + smolensk c mode/kind/os_version
        snaps = stub_session_and_callbacks["calls"]["snapshots"][0]["snapshots"]
        by_name = {s["name"]: s for s in snaps}
        assert by_name["1.7.5.9_orel_build"]["is_system"] is True
        assert by_name["1.7.5.9_orel_build"]["mode"] == "oryol"
        assert by_name["1.7.5.9_oryol"]["mode"] == "oryol"
        assert by_name["1.7.5.9_oryol"]["kind"] == "os_baseline"
        assert by_name["1.7.5.9_oryol"]["os_version"] == "1.7.5.9"
        assert by_name["1.7.5.9_smolensk"]["mode"] == "smolensk"
        # state-callback: plain-снимки без скрытого golden
        state = stub_session_and_callbacks["calls"]["vm_state"][0]
        assert state["snapshots"] == ["1.7.5.9_oryol", "1.7.5.9_smolensk"]
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
    async def test_single_box_grow_via_virt_resize(self, make_task, fetch_task, captured_audit, stub_session_and_callbacks):
        fake = _create_fake()
        # бокс 10G, запрошено 15G → рост (--expand)
        fake.set_response("qemu-img info", 0, '{"virtual-size": 10737418240}')
        stub_session_and_callbacks["holder"]["ssh"] = fake
        payload = {
            "vm_id": "vm2", "hub_host": "10.0.0.7", "name": "xfs-1",
            "cpu": 8, "ram_mb": 8192, "disk_gb": 15, "box": "xfs.box",
            "network_mode": "nat", "ip_address": None, "os_versions": [],
            "os_version": "1.8.1.6", "storage_pool_path": "/vms", "is_managed": True,
        }
        tid = await make_task(task_kind="vm.create", target_server_id="hub1", payload=payload)
        await vms.vm_create.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        cmds = fake.commands
        # virt-resize вместо cp+qemu-img resize; корневой раздел /dev/sda2
        assert any("qemu-img create -f qcow2 /vms/xfs-1.qcow2 15G" in c for c in cmds)
        assert any("virt-resize --expand /dev/sda2 /vms/xfs.box.qcow2 /vms/xfs-1.qcow2" in c for c in cmds)
        assert not any("cp /vms/xfs.box.qcow2" in c for c in cmds)
        assert not any("qemu-img resize" in c for c in cmds)
        # growpart-костыль убран
        assert not any("growpart" in c for c in cmds)
        assert any("network=test" in c for c in cmds)  # nat
        assert any("snapshot-create-as xfs-1 --name build" in c for c in cmds)
        # nat-режим статику в диск не льёт
        assert not any("virt-customize" in c for c in cmds)
        state = stub_session_and_callbacks["calls"]["vm_state"][0]
        assert state["snapshots"] == ["build"]
        snaps = stub_session_and_callbacks["calls"]["snapshots"][0]["snapshots"]
        assert snaps[0]["name"] == "build"
        assert snaps[0]["os_version"] == "1.8.1.6"

    async def test_single_box_shrink_offline_fs_then_virt_resize(self, make_task, fetch_task, captured_audit, stub_session_and_callbacks):
        fake = _create_fake()
        # бокс 20G, запрошено 10G → сжатие. ФС ужимается заранее в overlay бокса.
        fake.set_response("qemu-img info", 0, '{"virtual-size": 21474836480}')
        # минимум ФС ~5G — запрос на 10G проходит гард
        fake.set_response("vfs-minimum-size", 0, "5427978240\n")
        stub_session_and_callbacks["holder"]["ssh"] = fake
        payload = {
            "vm_id": "vm2", "hub_host": "10.0.0.7", "name": "xfs-1",
            "cpu": 8, "ram_mb": 8192, "disk_gb": 10, "box": "xfs.box",
            "network_mode": "nat", "ip_address": None, "os_versions": [],
            "storage_pool_path": "/vms", "is_managed": True,
        }
        tid = await make_task(task_kind="vm.create", target_server_id="hub1", payload=payload)
        await vms.vm_create.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        cmds = fake.commands
        work = "/vms/xfs-1.qcow2.shrink-src"
        # COW-overlay над боксом (сам бокс не трогаем — нет прямого virt-resize по нему)
        i_overlay = next(i for i, c in enumerate(cmds) if f"qemu-img create -f qcow2 -b /vms/xfs.box.qcow2 -F qcow2 {work}" in c)
        # offline-ужатие ext4 в overlay: e2fsck + resize2fs-size (10G - 256MiB)
        i_shrinkfs = next(i for i, c in enumerate(cmds) if f"guestfish -a {work} run" in c and "resize2fs-size /dev/sda2 10468982784" in c)
        # целевой диск на 10G + virt-resize --shrink из overlay (не из бокса)
        i_create = next(i for i, c in enumerate(cmds) if "qemu-img create -f qcow2 /vms/xfs-1.qcow2 10G" in c)
        i_resize = next(i for i, c in enumerate(cmds) if f"virt-resize --shrink /dev/sda2 {work} /vms/xfs-1.qcow2" in c)
        assert i_overlay < i_shrinkfs < i_create < i_resize
        # overlay бокса virt-resize'ом напрямую не сжимаем
        assert not any("virt-resize --shrink /dev/sda2 /vms/xfs.box.qcow2" in c for c in cmds)
        # overlay подчищается
        assert any(f"rm -f {work}" in c for c in cmds)

    async def test_single_box_shrink_below_used_rejected_before_work(self, make_task, fetch_task, captured_audit, stub_session_and_callbacks):
        fake = _create_fake()
        fake.set_response("qemu-img info", 0, '{"virtual-size": 21474836480}')
        # минимум ФС ~5G, запрос на 5G < минимум+буфер → отбой ДО overlay/virt-resize
        fake.set_response("vfs-minimum-size", 0, "5427978240\n")
        stub_session_and_callbacks["holder"]["ssh"] = fake
        payload = {
            "vm_id": "vm2b", "hub_host": "10.0.0.7", "name": "tiny-1",
            "cpu": 4, "ram_mb": 4096, "disk_gb": 5, "box": "xfs.box",
            "network_mode": "nat", "ip_address": None, "os_versions": [],
            "storage_pool_path": "/vms", "is_managed": True,
        }
        tid = await make_task(task_kind="vm.create", target_server_id="hub1", payload=payload)
        await _set_single_attempt(tid)
        await vms.vm_create.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.FAILED
        assert t.last_error and "VM_CREATE_FAILED" in t.last_error
        cmds = fake.commands
        # никакой тяжёлой работы: ни overlay, ни ужатия ФС, ни virt-resize
        assert not any("shrink-src" in c for c in cmds)
        assert not any("resize2fs-size" in c for c in cmds)
        assert not any("virt-resize" in c for c in cmds)

    async def test_single_bridge_injects_static_before_install(self, make_task, fetch_task, captured_audit, stub_session_and_callbacks):
        fake = _create_fake()
        fake.set_response("command -v virt-customize", 0)  # libguestfs уже стоит
        fake.set_response("mktemp", 0, "/tmp/dbos-if")
        stub_session_and_callbacks["holder"]["ssh"] = fake
        payload = {
            "vm_id": "vm3", "hub_host": "10.0.0.7", "name": "single-1",
            "cpu": 4, "ram_mb": 4096, "disk_gb": 0, "box": "single-box",
            "network_mode": "bridge", "ip_address": "10.177.103.108",
            "gateway": "10.177.103.254", "netmask": "255.255.255.0",
            "dns": ["10.177.180.246", "10.177.180.247"],
            "storage_pool_path": "/vms", "os_versions": [], "is_managed": True,
        }
        tid = await make_task(task_kind="vm.create", target_server_id="hub1", payload=payload)
        await vms.vm_create.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        cmds = fake.commands
        # порядок: клон диска → offline-инъекция статики → virt-install
        i_cp = next(i for i, c in enumerate(cmds) if "cp /vms/single-box.qcow2 /vms/single-1.qcow2" in c)
        i_customize = next(
            i for i, c in enumerate(cmds)
            if "virt-customize -a /vms/single-1.qcow2 " in c
            and "--upload /tmp/dbos-if:/etc/network/interfaces" in c
        )
        i_install = next(i for i, c in enumerate(cmds) if "virt-install -n single-1" in c)
        assert i_cp < i_customize < i_install
        # конфиг ушёл на stdin mktemp-файла со статикой из пула
        tee_idx = next(i for i, c in enumerate(cmds) if "tee /tmp/dbos-if" in c)
        body = fake.stdins[tee_idx] or ""
        assert "address 10.177.103.108" in body
        assert "gateway 10.177.103.254" in body
        assert "netmask 255.255.255.0" in body
        assert "dns-nameservers 10.177.180.246 10.177.180.247" in body
        state = stub_session_and_callbacks["calls"]["vm_state"][0]
        assert state["ip_address"] == "10.177.103.108"
        assert state["snapshots"] == ["build"]


class TestVmCreateHostnameAndAccounts:
    async def test_hostname_from_payload_and_etc_hosts(self, make_task, fetch_task, captured_audit, stub_session_and_callbacks):
        fake = _create_fake()
        stub_session_and_callbacks["holder"]["ssh"] = fake
        payload = {
            "vm_id": "vm5", "hub_host": "10.0.0.7", "name": "vm-5",
            "hostname": "station-hostname", "cpu": 4, "ram_mb": 4096,
            "disk_gb": 0, "box": "single-box", "network_mode": "nat",
            "ip_address": None, "os_versions": [], "storage_pool_path": "/vms",
            "is_managed": True,
        }
        tid = await make_task(task_kind="vm.create", target_server_id="hub1", payload=payload)
        await vms.vm_create.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        cmds = fake.commands
        assert any("hostnamectl set-hostname station-hostname" in c for c in cmds)
        # /etc/hosts запись, чтобы sudo не ругался unable to resolve host
        assert any("127.0.1.1 station-hostname" in c and "/etc/hosts" in c for c in cmds)
        # hostname != имя ВМ → домен всё равно зовётся vm-5
        assert any("virt-install -n vm-5" in c for c in cmds)

    async def test_hostname_defaults_to_name(self, make_task, fetch_task, captured_audit, stub_session_and_callbacks):
        fake = _create_fake()
        stub_session_and_callbacks["holder"]["ssh"] = fake
        payload = {
            "vm_id": "vm6", "hub_host": "10.0.0.7", "name": "vm-6",
            "cpu": 4, "ram_mb": 4096, "disk_gb": 0, "box": "single-box",
            "network_mode": "nat", "ip_address": None, "os_versions": [],
            "storage_pool_path": "/vms", "is_managed": True,
        }
        tid = await make_task(task_kind="vm.create", target_server_id="hub1", payload=payload)
        await vms.vm_create.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        assert any("hostnamectl set-hostname vm-6" in c for c in fake.commands)

    async def test_bound_accounts_provisioned_in_guest(self, make_task, fetch_task, captured_audit, stub_session_and_callbacks):
        fake = _create_fake()
        stub_session_and_callbacks["holder"]["ssh"] = fake
        payload = {
            "vm_id": "vm7", "hub_host": "10.0.0.7", "name": "vm-7",
            "cpu": 4, "ram_mb": 4096, "disk_gb": 0, "box": "single-box",
            "network_mode": "nat", "ip_address": None, "os_versions": [],
            "storage_pool_path": "/vms", "is_managed": True,
            "accounts": [
                {"account_id": "acc1", "login": "alice", "password": "pw-alice",
                 "has_sudo": True, "unix_groups": ["dev"]},
                {"account_id": "acc2", "login": "bob", "password": "pw-bob"},
            ],
        }
        tid = await make_task(task_kind="vm.create", target_server_id="hub1", payload=payload)
        await vms.vm_create.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        cmds = fake.commands
        # useradd по каждому аккаунту (идемпотентно через id-guard)
        assert any("id alice" in c and "useradd -m" in c and "alice" in c for c in cmds)
        assert any("id bob" in c and "useradd -m" in c for c in cmds)
        # sudo + доп.группа для alice
        assert any("usermod -aG dev,sudo alice" in c for c in cmds)
        # bob без групп → usermod не звался по нему для групп
        assert not any("usermod -aG" in c and "bob" in c for c in cmds)
        # пароли обоих через chpasswd
        assert any("alice:pw-alice | chpasswd" in c for c in cmds)
        assert any("bob:pw-bob | chpasswd" in c for c in cmds)

    async def test_account_password_fetched_via_internal(self, make_task, fetch_task, captured_audit, stub_session_and_callbacks, monkeypatch):
        fake = _create_fake()
        stub_session_and_callbacks["holder"]["ssh"] = fake

        async def _fetch(server_id, account_id, target_department_id=None):  # noqa: ARG001
            return {"login": "carol", "password": "fetched-pw"}

        monkeypatch.setattr(vms.server_service_client, "fetch_account_password", _fetch)
        payload = {
            "vm_id": "vm8", "hub_host": "10.0.0.7", "name": "vm-8",
            "cpu": 4, "ram_mb": 4096, "disk_gb": 0, "box": "single-box",
            "network_mode": "nat", "ip_address": None, "os_versions": [],
            "storage_pool_path": "/vms", "is_managed": True,
            "accounts": [
                {"account_id": "acc3", "server_id": "srv1", "login": "carol"},
            ],
        }
        tid = await make_task(task_kind="vm.create", target_server_id="hub1", payload=payload)
        await vms.vm_create.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        assert any("carol:fetched-pw | chpasswd" in c for c in fake.commands)

    async def test_account_password_fetched_by_id_when_no_server(self, make_task, fetch_task, captured_audit, stub_session_and_callbacks, monkeypatch):
        # dispatch vm.create несёт только account_id/login (без server_id) →
        # пароль тянем по одному account_id.
        fake = _create_fake()
        stub_session_and_callbacks["holder"]["ssh"] = fake
        seen: dict = {}

        async def _fetch_by_id(account_id, target_department_id=None):
            seen["account_id"] = account_id
            seen["dept"] = target_department_id
            return {"login": "dave", "password": "byid-pw"}

        monkeypatch.setattr(vms.server_service_client, "fetch_account_password_by_id", _fetch_by_id)
        payload = {
            "vm_id": "vm9", "hub_host": "10.0.0.7", "name": "vm-9",
            "cpu": 4, "ram_mb": 4096, "disk_gb": 0, "box": "single-box",
            "network_mode": "nat", "ip_address": None, "os_versions": [],
            "storage_pool_path": "/vms", "is_managed": True,
            "target_department_id": "dep9",
            "accounts": [{"account_id": "acc9", "login": "dave"}],
        }
        tid = await make_task(task_kind="vm.create", target_server_id="hub1", payload=payload)
        await vms.vm_create.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        assert seen == {"account_id": "acc9", "dept": "dep9"}
        cmds = fake.commands
        assert any("id dave" in c and "useradd -m" in c for c in cmds)
        assert any("dave:byid-pw | chpasswd" in c for c in cmds)

    async def test_account_created_even_when_password_unavailable(self, make_task, fetch_task, captured_audit, stub_session_and_callbacks, monkeypatch):
        # Пароль недоступен (нет эндпоинта/доступа) → учётку всё равно заводим,
        # только без chpasswd. Провал fetch НЕ роняет vm.create.
        from src.core.exceptions import CredentialFetchError

        fake = _create_fake()
        stub_session_and_callbacks["holder"]["ssh"] = fake

        async def _boom(account_id, target_department_id=None):  # noqa: ARG001
            raise CredentialFetchError(
                error_code="ACCOUNT_PASSWORD_UNAVAILABLE", message="nope",
            )

        monkeypatch.setattr(vms.server_service_client, "fetch_account_password_by_id", _boom)
        payload = {
            "vm_id": "vm10", "hub_host": "10.0.0.7", "name": "vm-10",
            "cpu": 4, "ram_mb": 4096, "disk_gb": 0, "box": "single-box",
            "network_mode": "nat", "ip_address": None, "os_versions": [],
            "storage_pool_path": "/vms", "is_managed": True,
            "accounts": [{"account_id": "acc10", "login": "erin"}],
        }
        tid = await make_task(task_kind="vm.create", target_server_id="hub1", payload=payload)
        await vms.vm_create.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        cmds = fake.commands
        assert any("id erin" in c and "useradd -m" in c for c in cmds)
        assert not any("erin:" in c and "chpasswd" in c for c in cmds)

    async def test_guest_commands_are_posix_quoted(self, make_task, fetch_task, captured_audit, stub_session_and_callbacks):
        # Регрессия на реальный прод-баг: guest-команда с вложенным `bash -c '...'`
        # должна быть валидным одним shell-словом. repr давал `\'` внутри
        # одинарных кавычек и рвал команду на hub'е ещё до гостя.
        import shlex

        fake = _create_fake()
        stub_session_and_callbacks["holder"]["ssh"] = fake
        payload = {
            "vm_id": "vm11", "hub_host": "10.0.0.7", "name": "vm-11",
            "hostname": "host11", "cpu": 4, "ram_mb": 4096, "disk_gb": 0,
            "box": "single-box", "network_mode": "nat", "ip_address": None,
            "os_versions": [], "storage_pool_path": "/vms", "is_managed": True,
            "accounts": [{"account_id": "acc11", "login": "frank", "password": "pw"}],
        }
        tid = await make_task(task_kind="vm.create", target_server_id="hub1", payload=payload)
        await vms.vm_create.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        guest_cmds = [c for c in fake.commands if "sshpass -e ssh" in c]
        assert guest_cmds
        for c in guest_cmds:
            # ни одной shell-невалидной последовательности \' внутри кавычек
            assert "\\'" not in c
            # весь argv парсится как валидные слова (иначе ValueError)
            shlex.split(c)


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
