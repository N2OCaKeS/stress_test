"""Тесты worker-тасок VM-менеджера волны 5: `vm.set_autostart` /
`vms_hub.teardown` / `vm.console_prep`.

SSH мокается `_FakeSshClient` (дефолт rc=0, точечные ответы по подстроке
команды) — как в `test_vms_tasks`. Ассертим последовательности `ssh.run`,
порядок teardown'а (destroy → undefine → pool cleanup) и internal-callback'и в
server_service.
"""

from __future__ import annotations

import pytest
from sqlalchemy import update

from src.core.constants import TaskStatus
from src.db.session import AsyncSessionLocal
from src.models import Task
from src.tasks import _vms_helpers, vms_lifecycle


# ── SSH mock ─────────────────────────────────────────────────────────────────


class _FakeSshClient:
    """Мок SshClient: пишет (command, stdin, sudo), отдаёт заданный ответ по
    подстроке команды. Дефолт — (0, "", "") (команда «прошла»)."""

    def __init__(self, host: str = "10.0.0.7"):
        self.host = host
        self._responses: list[tuple[str, tuple[int, str, str]]] = []
        self.commands: list[str] = []
        self.calls: list[tuple[str, bool]] = []

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
        self.calls.append((command, sudo))
        for pat, resp in self._responses:
            if pat in command:
                return resp
        return (0, "", "")


def _assert_root_libvirt(calls: list[tuple[str, bool]], needle: str) -> None:
    """Команда с подстрокой `needle` идёт в system-libvirt под root (sudo)."""
    matched = [(cmd, sudo) for cmd, sudo in calls if needle in cmd]
    assert matched, f"команда {needle!r} не найдена"
    for cmd, sudo in matched:
        assert "LC_ALL=C LIBGUESTFS_BACKEND=direct" in cmd, cmd
        assert sudo is True, cmd


@pytest.fixture(autouse=True)
def stub_session_and_callbacks(monkeypatch):
    """Замокать open_hub_session и три wave-5 callback'а.

    Тест кладёт свой fake в `holder['ssh']` до запуска таски. Возвращает dict со
    списками вызовов callback'ов.
    """
    holder: dict = {"ssh": None}
    calls: dict = {"vm_state": [], "torn_down": []}

    async def _open(payload):  # noqa: ARG001
        fake = holder["ssh"]
        return fake, fake.host

    monkeypatch.setattr(vms_lifecycle, "open_hub_session", _open)

    async def _vm_state(vm_id, target_department_id=None, **kw):
        calls["vm_state"].append({"vm_id": vm_id, "target_department_id": target_department_id, **kw})
        return {"ok": True}

    async def _torn_down(server_id, torn_down, target_department_id=None, **kw):
        calls["torn_down"].append({"server_id": server_id, "torn_down": torn_down, "target_department_id": target_department_id, **kw})
        return {"ok": True}

    monkeypatch.setattr(vms_lifecycle.server_service_client, "submit_vm_state", _vm_state)
    monkeypatch.setattr(vms_lifecycle.server_service_client, "submit_vms_hub_torn_down", _torn_down)
    return {"holder": holder, "calls": calls}


async def _set_single_attempt(tid: str):
    async with AsyncSessionLocal() as session:
        await session.execute(update(Task).where(Task.id == tid).values(max_attempts=1))
        await session.commit()


# ── чистый хелпер ────────────────────────────────────────────────────────────


class TestParseVncdisplay:
    def test_parse(self):
        assert _vms_helpers.parse_vncdisplay(":0") == 5900
        assert _vms_helpers.parse_vncdisplay("127.0.0.1:1") == 5901
        assert _vms_helpers.parse_vncdisplay("0.0.0.0:12\n") == 5912
        assert _vms_helpers.parse_vncdisplay("") is None
        assert _vms_helpers.parse_vncdisplay("no display") is None


class TestParseDisplayUri:
    def test_parse(self):
        # domdisplay отдаёт реальный порт в URI (не смещение 5900)
        assert _vms_helpers.parse_display_uri("spice://0.0.0.0:5900") == 5900
        assert _vms_helpers.parse_display_uri("spice://127.0.0.1:5901\n") == 5901
        assert _vms_helpers.parse_display_uri("vnc://0.0.0.0:5905") == 5905
        assert _vms_helpers.parse_display_uri("") is None
        assert _vms_helpers.parse_display_uri("no uri") is None


# ── vm.set_autostart ─────────────────────────────────────────────────────────


class TestVmSetAutostart:
    @pytest.mark.parametrize("enabled,fragment", [
        (True, "virsh autostart station-a"),
        (False, "virsh autostart --disable station-a"),
    ])
    async def test_autostart_on_off(self, enabled, fragment, make_task, fetch_task, captured_audit, stub_session_and_callbacks):
        fake = _FakeSshClient()
        stub_session_and_callbacks["holder"]["ssh"] = fake
        payload = {
            "vm_id": "vm1", "hub_host": "10.0.0.7", "vm_name": "station-a",
            "autostart": enabled, "is_managed": True, "target_department_id": "dep1",
        }
        tid = await make_task(task_kind="vm.set_autostart", target_server_id="hub1", payload=payload)
        await vms_lifecycle.vm_set_autostart.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        assert any(fragment in c for c in fake.commands)
        # --disable отсутствует в enabled-ветке
        if enabled:
            assert not any("--disable" in c for c in fake.commands)
        state = stub_session_and_callbacks["calls"]["vm_state"][0]
        assert state == {"vm_id": "vm1", "target_department_id": "dep1", "autostart": enabled}
        assert t.result["autostart"] is enabled

    async def test_legacy_enabled_field_still_read(self, make_task, fetch_task, captured_audit, stub_session_and_callbacks):
        # Старый payload'ный ключ `enabled` работает как фолбэк.
        fake = _FakeSshClient()
        stub_session_and_callbacks["holder"]["ssh"] = fake
        payload = {
            "vm_id": "vm1", "hub_host": "10.0.0.7", "vm_name": "station-a",
            "enabled": True, "is_managed": True, "target_department_id": "dep1",
        }
        tid = await make_task(task_kind="vm.set_autostart", target_server_id="hub1", payload=payload)
        await vms_lifecycle.vm_set_autostart.original_func(tid)
        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        assert any("virsh autostart station-a" in c for c in fake.commands)
        assert t.result["autostart"] is True

    async def test_non_bool_enabled_rejected(self, make_task, fetch_task, captured_audit, stub_session_and_callbacks):
        fake = _FakeSshClient()
        stub_session_and_callbacks["holder"]["ssh"] = fake
        payload = {"vm_id": "vm1", "hub_host": "10.0.0.7", "vm_name": "s", "enabled": "yes", "is_managed": True}
        tid = await make_task(task_kind="vm.set_autostart", target_server_id="hub1", payload=payload)
        await _set_single_attempt(tid)
        await vms_lifecycle.vm_set_autostart.original_func(tid)
        t = await fetch_task(tid)
        assert t.status == TaskStatus.FAILED
        assert t.last_error and "VM_INVALID_ARG" in t.last_error
        # валидация аргумента — до открытия сессии, callback'а нет (нет busy-lock)
        assert stub_session_and_callbacks["calls"]["vm_state"] == []

    async def test_autostart_failure_reports_error(self, make_task, fetch_task, captured_audit, stub_session_and_callbacks):
        fake = _FakeSshClient()
        fake.set_response("virsh autostart", 1, stderr="boom")
        stub_session_and_callbacks["holder"]["ssh"] = fake
        payload = {"vm_id": "vm1", "hub_host": "10.0.0.7", "vm_name": "station-a", "enabled": True, "is_managed": True}
        tid = await make_task(task_kind="vm.set_autostart", target_server_id="hub1", payload=payload)
        await _set_single_attempt(tid)
        await vms_lifecycle.vm_set_autostart.original_func(tid)
        t = await fetch_task(tid)
        assert t.status == TaskStatus.FAILED
        assert t.last_error and "VM_AUTOSTART_FAILED" in t.last_error
        assert stub_session_and_callbacks["calls"]["vm_state"][0]["error"] == "VM_AUTOSTART_FAILED"


# ── vms_hub.teardown ─────────────────────────────────────────────────────────


class TestVmsHubTeardown:
    async def test_full_teardown_order(self, make_task, fetch_task, captured_audit, stub_session_and_callbacks):
        fake = _FakeSshClient()
        stub_session_and_callbacks["holder"]["ssh"] = fake
        payload = {
            "server_id": "hub1", "host": "10.0.0.7", "os_family": "apt",
            "vms": ["station-a", "station-b"], "storage_pool_path": "/vms",
            "purge_packages": True, "remove_bridge": True, "phy_if": "ens192",
            "is_managed": True, "target_department_id": "dep1",
        }
        tid = await make_task(task_kind="vms_hub.teardown", target_server_id="hub1", payload=payload)
        await vms_lifecycle.vms_hub_teardown.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        cmds = fake.commands
        # destroy предшествует undefine для каждой ВМ
        for name in ("station-a", "station-b"):
            di = next(i for i, c in enumerate(cmds) if f"virsh destroy {name}" in c)
            ui = next(i for i, c in enumerate(cmds) if f"virsh undefine {name} --remove-all-storage" in c)
            assert di < ui
        # undefine ВМ идёт раньше сноса пулов
        last_undefine = max(i for i, c in enumerate(cmds) if "virsh undefine station" in c)
        pool_destroy = next(i for i, c in enumerate(cmds) if "virsh pool-destroy vms" in c)
        assert last_undefine < pool_destroy
        # пулы: additional + vms, снос каталога, purge, br0
        assert any("virsh pool-destroy additional" in c for c in cmds)
        assert any("virsh pool-undefine vms" in c for c in cmds)
        assert any("rm -rf /vms" in c for c in cmds)
        assert any("apt-get purge -y astra-kvm" in c for c in cmds)
        assert any("interfaces.d/dbos-br0.cfg" in c for c in cmds)
        assert any("ip link delete br0" in c for c in cmds)
        # libvirt-операции (destroy/undefine/pool) — под root (system-libvirt)
        _assert_root_libvirt(fake.calls, "virsh destroy station-a")
        _assert_root_libvirt(fake.calls, "virsh undefine station-a --remove-all-storage")
        _assert_root_libvirt(fake.calls, "virsh pool-destroy vms")
        _assert_root_libvirt(fake.calls, "virsh pool-undefine additional")
        # системные шаги хоста остаются под sudo
        assert any(sudo and "apt-get purge -y astra-kvm" in cmd for cmd, sudo in fake.calls)
        assert any(sudo and "rm -rf /vms" in cmd for cmd, sudo in fake.calls)
        # callback torn_down=True + список снятых ВМ
        assert stub_session_and_callbacks["calls"]["torn_down"] == [
            {"server_id": "hub1", "torn_down": True, "target_department_id": "dep1",
             "removed_vms": ["station-a", "station-b"]},
        ]

    async def test_minimal_teardown_no_purge_no_bridge(self, make_task, fetch_task, captured_audit, stub_session_and_callbacks):
        fake = _FakeSshClient()
        stub_session_and_callbacks["holder"]["ssh"] = fake
        payload = {
            "server_id": "hub1", "host": "10.0.0.7", "os_family": "apt",
            "vms": [], "is_managed": True,
        }
        tid = await make_task(task_kind="vms_hub.teardown", target_server_id="hub1", payload=payload)
        await vms_lifecycle.vms_hub_teardown.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        cmds = fake.commands
        assert not any("apt-get purge" in c for c in cmds)
        assert not any("br0" in c for c in cmds)
        # пулы всё равно сносим (пустой список ВМ)
        assert any("virsh pool-destroy vms" in c for c in cmds)
        assert any("rm -rf /vms" in c for c in cmds)
        torn = stub_session_and_callbacks["calls"]["torn_down"][0]
        assert torn["torn_down"] is True
        assert torn["removed_vms"] == []
        assert t.result["purged"] is False
        assert t.result["bridge_removed"] is False

    async def test_success_ack_failure_does_not_fail_task(self, make_task, fetch_task, captured_audit, stub_session_and_callbacks, monkeypatch):
        # Хост разобран; недоступный/незаведённый torn-down ack не должен ронять
        # успешную таску (best-effort).
        fake = _FakeSshClient()
        stub_session_and_callbacks["holder"]["ssh"] = fake

        async def _boom(*a, **k):  # noqa: ARG001
            raise RuntimeError("no such internal endpoint")

        monkeypatch.setattr(vms_lifecycle.server_service_client, "submit_vms_hub_torn_down", _boom)
        payload = {
            "server_id": "hub1", "host": "10.0.0.7", "os_family": "apt",
            "vms": ["v1"], "is_managed": True,
        }
        tid = await make_task(task_kind="vms_hub.teardown", target_server_id="hub1", payload=payload)
        await vms_lifecycle.vms_hub_teardown.original_func(tid)
        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        assert t.result["torn_down"] is True

    async def test_dnf_purge_uses_dnf(self, make_task, fetch_task, captured_audit, stub_session_and_callbacks):
        fake = _FakeSshClient()
        stub_session_and_callbacks["holder"]["ssh"] = fake
        payload = {
            "server_id": "hub1", "host": "10.0.0.7", "os_family": "dnf",
            "vms": ["v1"], "purge_packages": True, "is_managed": True,
        }
        tid = await make_task(task_kind="vms_hub.teardown", target_server_id="hub1", payload=payload)
        await vms_lifecycle.vms_hub_teardown.original_func(tid)
        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        assert any("dnf remove -y qemu-kvm libvirt" in c for c in fake.commands)
        assert not any("apt-get purge" in c for c in fake.commands)

    async def test_undefine_failure_reports_error(self, make_task, fetch_task, captured_audit, stub_session_and_callbacks):
        fake = _FakeSshClient()
        fake.set_response("virsh undefine station-a", 2, stderr="in use")
        stub_session_and_callbacks["holder"]["ssh"] = fake
        payload = {
            "server_id": "hub1", "host": "10.0.0.7", "os_family": "apt",
            "vms": ["station-a"], "is_managed": True, "target_department_id": "dep1",
        }
        tid = await make_task(task_kind="vms_hub.teardown", target_server_id="hub1", payload=payload)
        await _set_single_attempt(tid)
        await vms_lifecycle.vms_hub_teardown.original_func(tid)
        t = await fetch_task(tid)
        assert t.status == TaskStatus.FAILED
        assert t.last_error and "VMS_HUB_TEARDOWN_FAILED" in t.last_error
        torn = stub_session_and_callbacks["calls"]["torn_down"][0]
        assert torn["torn_down"] is False
        assert torn["error"] == "VMS_HUB_TEARDOWN_FAILED"


# ── vm.console_prep ──────────────────────────────────────────────────────────


class TestVmConsolePrep:
    async def test_existing_vnc_returns_port(self, make_task, fetch_task, captured_audit, stub_session_and_callbacks):
        fake = _FakeSshClient()
        fake.set_response(
            "virsh dumpxml", 0,
            "<domain><devices><graphics type='vnc' port='5900'/>"
            "<serial type='pty'/></devices></domain>",
        )
        fake.set_response("virsh vncdisplay", 0, "127.0.0.1:0")
        stub_session_and_callbacks["holder"]["ssh"] = fake
        payload = {
            "vm_id": "vm1", "hub_host": "10.0.0.7", "vm_name": "station-a",
            "is_managed": True, "target_department_id": "dep1",
        }
        tid = await make_task(task_kind="vm.console_prep", target_server_id="hub1", payload=payload)
        await vms_lifecycle.vm_console_prep.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        # VNC уже есть → --add-device --graphics не вызывается
        assert not any("--graphics" in c for c in fake.commands)
        # serial уже есть → не добавляем
        assert not any("--serial" in c for c in fake.commands)
        assert any("virsh vncdisplay station-a" in c for c in fake.commands)
        # dumpxml/vncdisplay — под root
        _assert_root_libvirt(fake.calls, "virsh dumpxml station-a")
        _assert_root_libvirt(fake.calls, "virsh vncdisplay station-a")
        # Порт дисплея уезжает graphics_port'ом в state-callback.
        state = stub_session_and_callbacks["calls"]["vm_state"][0]
        assert state["graphics_port"] == 5900
        assert t.result["vnc_port"] == 5900
        assert t.result["graphics_type"] == "vnc"
        assert t.result["serial_ready"] is True

    async def test_adds_vnc_and_serial_when_absent(self, make_task, fetch_task, captured_audit, stub_session_and_callbacks):
        fake = _FakeSshClient()
        fake.set_response("virsh dumpxml", 0, "<domain><devices></devices></domain>")
        fake.set_response("virsh vncdisplay", 0, "0.0.0.0:2")
        stub_session_and_callbacks["holder"]["ssh"] = fake
        payload = {
            "vm_id": "vm1", "hub_host": "10.0.0.7", "vm_name": "station-a",
            "vnc_listen": "10.177.103.207", "serial": True, "is_managed": True,
        }
        tid = await make_task(task_kind="vm.console_prep", target_server_id="hub1", payload=payload)
        await vms_lifecycle.vm_console_prep.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        cmds = fake.commands
        assert any("virt-xml station-a --add-device --graphics type=vnc,listen=10.177.103.207,port=-1" in c for c in cmds)
        assert any("virt-xml station-a --add-device --serial pty" in c for c in cmds)
        # virt-xml добавление graphics/serial — под root
        _assert_root_libvirt(fake.calls, "virt-xml station-a --add-device --graphics type=vnc")
        _assert_root_libvirt(fake.calls, "virt-xml station-a --add-device --serial pty")
        state = stub_session_and_callbacks["calls"]["vm_state"][0]
        assert state["graphics_port"] == 5902
        assert t.result["vnc_listen"] == "10.177.103.207"

    async def test_serial_disabled(self, make_task, fetch_task, captured_audit, stub_session_and_callbacks):
        fake = _FakeSshClient()
        fake.set_response("virsh dumpxml", 0, "<domain><devices></devices></domain>")
        fake.set_response("virsh vncdisplay", 0, ":0")
        stub_session_and_callbacks["holder"]["ssh"] = fake
        payload = {
            "vm_id": "vm1", "hub_host": "10.0.0.7", "vm_name": "s",
            "serial": False, "is_managed": True,
        }
        tid = await make_task(task_kind="vm.console_prep", target_server_id="hub1", payload=payload)
        await vms_lifecycle.vm_console_prep.original_func(tid)
        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        assert not any("--serial" in c for c in fake.commands)
        assert t.result["serial_ready"] is False

    async def test_graphics_add_failure_reports_error(self, make_task, fetch_task, captured_audit, stub_session_and_callbacks):
        fake = _FakeSshClient()
        fake.set_response("virsh dumpxml", 0, "<domain><devices></devices></domain>")
        fake.set_response("virt-xml", 1, stderr="boom")
        stub_session_and_callbacks["holder"]["ssh"] = fake
        payload = {"vm_id": "vm1", "hub_host": "10.0.0.7", "vm_name": "s", "is_managed": True, "target_department_id": "dep1"}
        tid = await make_task(task_kind="vm.console_prep", target_server_id="hub1", payload=payload)
        await _set_single_attempt(tid)
        await vms_lifecycle.vm_console_prep.original_func(tid)
        t = await fetch_task(tid)
        assert t.status == TaskStatus.FAILED
        assert t.last_error and "VM_CONSOLE_PREP_FAILED" in t.last_error
        assert stub_session_and_callbacks["calls"]["vm_state"][0]["error"] == "VM_CONSOLE_PREP_FAILED"

    async def test_spice_adds_device_and_reads_domdisplay_port(self, make_task, fetch_task, captured_audit, stub_session_and_callbacks):
        fake = _FakeSshClient()
        # у домена только vnc → для spice надо добавить устройство
        fake.set_response(
            "virsh dumpxml", 0,
            "<domain><devices><graphics type='vnc'/></devices></domain>",
        )
        fake.set_response("virsh domdisplay --type spice", 0, "spice://0.0.0.0:5905")
        stub_session_and_callbacks["holder"]["ssh"] = fake
        payload = {
            "vm_id": "vm1", "hub_host": "10.0.0.7", "vm_name": "station-a",
            "graphics": "spice", "serial": False, "is_managed": True,
            "target_department_id": "dep1",
        }
        tid = await make_task(task_kind="vm.console_prep", target_server_id="hub1", payload=payload)
        await vms_lifecycle.vm_console_prep.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        cmds = fake.commands
        assert any("virt-xml station-a --add-device --graphics type=spice,listen=0.0.0.0,port=-1" in c for c in cmds)
        # vncdisplay для spice-пути не читаем
        assert not any("virsh vncdisplay" in c for c in cmds)
        assert any("virsh domdisplay --type spice station-a" in c for c in cmds)
        state = stub_session_and_callbacks["calls"]["vm_state"][0]
        assert state["graphics_port"] == 5905
        assert t.result["spice_port"] == 5905
        assert t.result["graphics_type"] == "spice"

    async def test_spice_already_present_not_readded(self, make_task, fetch_task, captured_audit, stub_session_and_callbacks):
        fake = _FakeSshClient()
        fake.set_response(
            "virsh dumpxml", 0,
            "<domain><devices><graphics type='spice' port='5900'/>"
            "<serial type='pty'/></devices></domain>",
        )
        fake.set_response("virsh domdisplay --type spice", 0, "spice://0.0.0.0:5900")
        stub_session_and_callbacks["holder"]["ssh"] = fake
        payload = {
            "vm_id": "vm1", "hub_host": "10.0.0.7", "vm_name": "station-a",
            "graphics": "spice", "is_managed": True,
        }
        tid = await make_task(task_kind="vm.console_prep", target_server_id="hub1", payload=payload)
        await vms_lifecycle.vm_console_prep.original_func(tid)
        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        # spice уже есть и serial уже есть → ничего не добавляем
        assert not any("--add-device" in c for c in fake.commands)
        assert stub_session_and_callbacks["calls"]["vm_state"][0]["graphics_port"] == 5900

    async def test_invalid_graphics_rejected(self, make_task, fetch_task, captured_audit, stub_session_and_callbacks):
        fake = _FakeSshClient()
        stub_session_and_callbacks["holder"]["ssh"] = fake
        payload = {
            "vm_id": "vm1", "hub_host": "10.0.0.7", "vm_name": "s",
            "graphics": "webrtc", "is_managed": True, "target_department_id": "dep1",
        }
        tid = await make_task(task_kind="vm.console_prep", target_server_id="hub1", payload=payload)
        await _set_single_attempt(tid)
        await vms_lifecycle.vm_console_prep.original_func(tid)
        t = await fetch_task(tid)
        assert t.status == TaskStatus.FAILED
        assert t.last_error and "VM_INVALID_ARG" in t.last_error
