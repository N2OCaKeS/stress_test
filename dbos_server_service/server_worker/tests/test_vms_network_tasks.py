"""Тесты worker-тасок VM-менеджера: `vm.prepare` / `vm.set_network`.

SSH мокается `_FakeSshClient` (дефолт rc=0, точечные ответы по подстроке
команды) — как в `test_vms_tasks`. Проверяем: prepare ставит mgmt-учётку +
ключ + sudo, проверяет вход по ключу ДО деструктива, хардит sshd и удаляет
базовую учётку `u` (безопасный порядок); set_network пишет статику и переводит
NIC на нужный мост (`br0` для LAN, `natbr0` для NAT); internal-callback'и.
"""

from __future__ import annotations

import pytest
from sqlalchemy import update

from src.clients.ssh import SshError
from src.core.constants import TaskStatus
from src.db.session import AsyncSessionLocal
from src.models import Task
from src.tasks import _vm_prepare_helpers, vms_network


# ── SSH mock ─────────────────────────────────────────────────────────────────


class _FakeSshClient:
    """Мок SshClient: пишет (command, stdin, sudo), отдаёт ответ по подстроке
    команды. Дефолт — (0, "", "")."""

    def __init__(self, host: str = "10.0.0.7"):
        self.host = host
        self._responses: list[tuple[str, tuple[int, str, str]]] = []
        self.commands: list[str] = []
        self.stdins: list[str | None] = []
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

    async def run(self, command, *, sudo=False, stdin_payload=None):
        self.commands.append(command)
        self.stdins.append(stdin_payload)
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


_MGMT = {
    "management_user": "dbos",
    "public_key": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIabc123 dbos@vm",
    "private_key": "-----BEGIN OPENSSH PRIVATE KEY-----\nabc\n-----END OPENSSH PRIVATE KEY-----\n",
    "password": "S3cretPass",
}


@pytest.fixture(autouse=True)
def stub_session_and_callbacks(monkeypatch):
    """Замокать hub-сессию, callback'и, чтение stash/маркера и таймеры сна.

    Тест кладёт свой fake в `holder['ssh']`. `holder['verified']` управляет
    веткой retry (маркер уже стоит). `calls` собирает callback'и.
    """
    holder: dict = {"ssh": None, "verified": False, "mgmt": dict(_MGMT)}
    calls: dict = {"prepared": [], "vm_state": []}

    async def _open(payload):  # noqa: ARG001
        fake = holder["ssh"]
        return fake, fake.host

    monkeypatch.setattr(vms_network, "open_hub_session", _open)

    async def _read_mgmt(stash_key):  # noqa: ARG001
        return holder["mgmt"]

    async def _read_marker(task_id):  # noqa: ARG001
        return holder["verified"]

    async def _noop(*a, **kw):
        return None

    monkeypatch.setattr(_vm_prepare_helpers, "_read_mgmt_install", _read_mgmt)
    monkeypatch.setattr(vms_network, "_read_verified_marker", _read_marker)
    monkeypatch.setattr(vms_network, "_set_verified_marker", _noop)
    monkeypatch.setattr(vms_network, "_delete_verified_marker", _noop)
    monkeypatch.setattr(vms_network, "_delete_stash", _noop)

    # Обнуляем задержки ожидания гостя/адреса — реальный ребут это минуты.
    monkeypatch.setattr(vms_network, "_GUEST_REBOOT_SETTLE_S", 0)
    monkeypatch.setattr(vms_network, "_GUEST_REBOOT_POLL_DELAY_S", 0)
    monkeypatch.setattr(vms_network, "_GUEST_REBOOT_MAX_POLLS", 3)
    monkeypatch.setattr(vms_network, "_GUEST_SSH_PROBE_ATTEMPTS", 2)
    monkeypatch.setattr(vms_network, "_GUEST_SSH_PROBE_DELAY_S", 0)

    async def _prepared(vm_id, management_user, target_department_id=None, **kw):
        calls["prepared"].append({
            "vm_id": vm_id, "management_user": management_user,
            "target_department_id": target_department_id, **kw,
        })
        return {"ok": True}

    async def _vm_state(vm_id, target_department_id=None, **kw):
        calls["vm_state"].append({
            "vm_id": vm_id, "target_department_id": target_department_id, **kw,
        })
        return {"ok": True}

    monkeypatch.setattr(vms_network.server_service_client, "submit_vm_prepared", _prepared)
    monkeypatch.setattr(vms_network.server_service_client, "submit_vm_state", _vm_state)
    return {"holder": holder, "calls": calls}


async def _set_single_attempt(tid: str):
    async with AsyncSessionLocal() as session:
        await session.execute(update(Task).where(Task.id == tid).values(max_attempts=1))
        await session.commit()


def _idx(cmds: list[str], needle: str) -> int:
    return next(i for i, c in enumerate(cmds) if needle in c)


# ── vm.prepare ───────────────────────────────────────────────────────────────


def _prepare_payload(**over):
    base = {
        "vm_id": "vm1", "hub_host": "10.0.0.7", "vm_name": "station-a",
        "guest_ip": "10.177.103.101", "creds_stash_key": "dbos:dispatch_creds:abc123",
        "is_managed": True, "target_department_id": "dep1",
    }
    base.update(over)
    return base


def _prepare_fake():
    fake = _FakeSshClient()
    fake.set_response("mktemp", 0, "/tmp/dbos-mgmt-key")
    return fake


class TestVmPrepare:
    async def test_full_bootstrap_and_safe_order(
        self, make_task, fetch_task, captured_audit, stub_session_and_callbacks,
    ):
        fake = _prepare_fake()
        stub_session_and_callbacks["holder"]["ssh"] = fake
        tid = await make_task(
            task_kind="vm.prepare", target_server_id="hub1",
            payload=_prepare_payload(),
        )
        await vms_network.vm_prepare.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        cmds = fake.commands
        # управляющий пользователь + sudo-группа + sudoers NOPASSWD
        assert any("useradd -m -s /bin/bash dbos" in c for c in cmds)
        assert any("usermod -aG sudo dbos" in c for c in cmds)
        assert any("NOPASSWD: ALL" in c and "sudoers.d/dbos-management" in c for c in cmds)
        # пароль + публичный ключ
        assert any("echo dbos:S3cretPass | chpasswd" in c for c in cmds)
        assert any("authorized_keys" in c and "ssh-ed25519" in c for c in cmds)
        # ключ записан во временный файл на hub'е и затёрт
        assert any("tee /tmp/dbos-mgmt-key" in c for c in cmds)
        assert any("shred -u /tmp/dbos-mgmt-key" in c for c in cmds)
        # безопасный порядок: verify по ключу ДО хардинга и удаления `u`
        i_verify = _idx(cmds, "sudo -n true")
        i_harden = _idx(cmds, "PasswordAuthentication no")
        i_del = _idx(cmds, "userdel -rf u")
        assert i_verify < i_harden < i_del
        # callback: mgmt-креды установлены
        assert stub_session_and_callbacks["calls"]["prepared"] == [
            {"vm_id": "vm1", "management_user": "dbos", "target_department_id": "dep1"},
        ]

    async def test_installs_guest_agent(
        self, make_task, fetch_task, captured_audit, stub_session_and_callbacks,
    ):
        fake = _prepare_fake()
        stub_session_and_callbacks["holder"]["ssh"] = fake
        tid = await make_task(
            task_kind="vm.prepare", target_server_id="hub1",
            payload=_prepare_payload(),
        )
        await vms_network.vm_prepare.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        # qemu-guest-agent ставится в гостя и включается — под парольной сессией.
        assert any(
            "qemu-guest-agent" in c and "enable --now" in c for c in fake.commands
        )

    async def test_verify_failure_preserves_base_user(
        self, make_task, fetch_task, captured_audit, stub_session_and_callbacks,
    ):
        fake = _prepare_fake()
        fake.set_response("sudo -n true", 255)  # вход по ключу не проходит
        stub_session_and_callbacks["holder"]["ssh"] = fake
        tid = await make_task(
            task_kind="vm.prepare", target_server_id="hub1",
            payload=_prepare_payload(),
        )
        await _set_single_attempt(tid)
        await vms_network.vm_prepare.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.FAILED
        assert t.last_error and "VM_PREPARE_KEY_VERIFY_FAILED" in t.last_error
        # базовую учётку НЕ удаляли, sshd НЕ хардили
        assert not any("userdel -rf u" in c for c in fake.commands)
        assert not any("PasswordAuthentication no" in c for c in fake.commands)
        # mgmt-callback не звали, но error-callback был
        assert stub_session_and_callbacks["calls"]["prepared"] == []
        assert stub_session_and_callbacks["calls"]["vm_state"][0]["error"] == \
            "VM_PREPARE_KEY_VERIFY_FAILED"

    async def test_retry_skips_password_bootstrap(
        self, make_task, fetch_task, captured_audit, stub_session_and_callbacks,
    ):
        # Маркер уже стоит — предыдущая попытка прошла verify, упала на финализе.
        stub_session_and_callbacks["holder"]["verified"] = True
        fake = _prepare_fake()
        stub_session_and_callbacks["holder"]["ssh"] = fake
        tid = await make_task(
            task_kind="vm.prepare", target_server_id="hub1",
            payload=_prepare_payload(),
        )
        await vms_network.vm_prepare.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        cmds = fake.commands
        # пароль-часть пропущена (парольный вход мог быть уже выключен)
        assert not any("useradd" in c for c in cmds)
        assert not any("chpasswd" in c for c in cmds)
        # но key-финализ добит: re-verify + harden + userdel
        assert any("sudo -n true" in c for c in cmds)
        assert any("userdel -rf u" in c for c in cmds)
        assert stub_session_and_callbacks["calls"]["prepared"][0]["management_user"] == "dbos"

    async def test_no_stash_key_fails(
        self, make_task, fetch_task, captured_audit, stub_session_and_callbacks,
    ):
        fake = _prepare_fake()
        stub_session_and_callbacks["holder"]["ssh"] = fake
        tid = await make_task(
            task_kind="vm.prepare", target_server_id="hub1",
            payload=_prepare_payload(creds_stash_key=None),
        )
        await _set_single_attempt(tid)
        await vms_network.vm_prepare.original_func(tid)
        t = await fetch_task(tid)
        assert t.status == TaskStatus.FAILED
        assert t.last_error and "DISPATCH_STASH_MISSING" in t.last_error

    async def test_bad_public_key_rejected(
        self, make_task, fetch_task, captured_audit, stub_session_and_callbacks,
    ):
        fake = _prepare_fake()
        stub_session_and_callbacks["holder"]["ssh"] = fake
        stub_session_and_callbacks["holder"]["mgmt"] = {
            **_MGMT, "public_key": "ssh-ed25519 AAA$(rm -rf /) x",
        }
        tid = await make_task(
            task_kind="vm.prepare", target_server_id="hub1",
            payload=_prepare_payload(),
        )
        await _set_single_attempt(tid)
        await vms_network.vm_prepare.original_func(tid)
        t = await fetch_task(tid)
        assert t.status == TaskStatus.FAILED
        assert t.last_error and "VM_INVALID_ARG" in t.last_error


# ── vm.set_network ───────────────────────────────────────────────────────────


class TestVmSetNetworkBridge:
    async def test_static_and_bridge_switch(
        self, make_task, fetch_task, captured_audit, stub_session_and_callbacks,
    ):
        fake = _FakeSshClient()
        fake.set_response("virsh domstate", 0, "running")
        stub_session_and_callbacks["holder"]["ssh"] = fake
        payload = {
            "vm_id": "vm1", "hub_host": "10.0.0.7", "vm_name": "station-a",
            "network_mode": "bridge", "ip_address": "10.177.103.101",
            "guest_ip": "192.168.100.24", "target_department_id": "dep1",
        }
        tid = await make_task(task_kind="vm.set_network", target_server_id="hub1", payload=payload)
        await vms_network.vm_set_network.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        cmds = fake.commands
        # статика: printf в /etc/network/interfaces с адресом/шлюзом
        i_static = _idx(cmds, "/etc/network/interfaces")
        assert "address 10.177.103.101" in cmds[i_static]
        assert "gateway 10.177.103.254" in cmds[i_static]
        assert "netmask 255.255.255.0" in cmds[i_static]
        # перевод NIC на bridge → рестарт домена (после записи статики)
        i_bridge = _idx(cmds, "virt-xml station-a --edit --network bridge=br0")
        assert i_static < i_bridge
        assert any("virsh start station-a" in c for c in cmds)
        # bridge остаётся bridge=br0; NIC-свитч и рестарт домена — под root
        _assert_root_libvirt(
            fake.calls, "virt-xml station-a --edit --network bridge=br0",
        )
        _assert_root_libvirt(fake.calls, "virsh start station-a")
        _assert_root_libvirt(fake.calls, "virsh destroy station-a")
        state = stub_session_and_callbacks["calls"]["vm_state"][0]
        assert state["ip_address"] == "10.177.103.101"
        assert state["power_state"] == "on"

    async def test_bridge_requires_ip(
        self, make_task, fetch_task, captured_audit, stub_session_and_callbacks,
    ):
        fake = _FakeSshClient()
        stub_session_and_callbacks["holder"]["ssh"] = fake
        payload = {
            "vm_id": "vm1", "hub_host": "10.0.0.7", "vm_name": "station-a",
            "network_mode": "bridge", "guest_ip": "192.168.100.24",
        }
        tid = await make_task(task_kind="vm.set_network", target_server_id="hub1", payload=payload)
        await _set_single_attempt(tid)
        await vms_network.vm_set_network.original_func(tid)
        t = await fetch_task(tid)
        assert t.status == TaskStatus.FAILED
        assert t.last_error and "VM_INVALID_ARG" in t.last_error


class TestVmSetNetworkBridgeFallback:
    async def test_virt_customize_when_guest_unreachable(
        self, make_task, fetch_task, captured_audit, stub_session_and_callbacks,
    ):
        fake = _FakeSshClient()
        fake.set_response("virsh domstate", 0, "running")
        # Гость на NAT-адресе по SSH не отвечает → уходим в offline-фолбэк.
        fake.set_response("u@192.168.100.24", 255)
        fake.set_response(
            "virsh domblklist", 0,
            " Target   Source\n"
            "---------------------------------\n"
            " vda      /vms/station-a.qcow2\n",
        )
        fake.set_response("command -v virt-customize", 0)  # libguestfs уже стоит
        fake.set_response("mktemp", 0, "/tmp/dbos-if")
        stub_session_and_callbacks["holder"]["ssh"] = fake
        payload = {
            "vm_id": "vm1", "hub_host": "10.0.0.7", "vm_name": "station-a",
            "network_mode": "bridge", "ip_address": "10.177.103.101",
            "guest_ip": "192.168.100.24", "target_department_id": "dep1",
        }
        tid = await make_task(
            task_kind="vm.set_network", target_server_id="hub1", payload=payload,
        )
        await vms_network.vm_set_network.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        cmds = fake.commands
        # статик-конфиг ушёл на stdin mktemp-файла, а не в госта по SSH
        assert not any(
            "printf" in c and "/etc/network/interfaces" in c for c in cmds
        )
        # offline-заливка правильного qcow2 через virt-customize
        i_customize = _idx(
            cmds,
            "virt-customize -a /vms/station-a.qcow2 ",
        )
        # порядок: destroy → virt-customize → start
        i_destroy = _idx(cmds, "virsh destroy station-a")
        i_start = _idx(cmds, "virsh start station-a")
        assert i_destroy < i_customize < i_start
        # NIC всё равно переведён на bridge
        assert any("virt-xml station-a --edit --network bridge=br0" in c for c in cmds)
        state = stub_session_and_callbacks["calls"]["vm_state"][0]
        assert state["ip_address"] == "10.177.103.101"

    async def test_missing_virt_customize_fails(
        self, make_task, fetch_task, captured_audit, stub_session_and_callbacks,
    ):
        fake = _FakeSshClient()
        fake.set_response("u@192.168.100.24", 255)  # гость недостижим
        fake.set_response(
            "virsh domblklist", 0, " vda      /vms/station-a.qcow2\n",
        )
        fake.set_response("command -v virt-customize", 1)  # бинаря нет и не встал
        stub_session_and_callbacks["holder"]["ssh"] = fake
        payload = {
            "vm_id": "vm1", "hub_host": "10.0.0.7", "vm_name": "station-a",
            "network_mode": "bridge", "ip_address": "10.177.103.101",
            "guest_ip": "192.168.100.24",
        }
        tid = await make_task(
            task_kind="vm.set_network", target_server_id="hub1", payload=payload,
        )
        await _set_single_attempt(tid)
        await vms_network.vm_set_network.original_func(tid)
        t = await fetch_task(tid)
        assert t.status == TaskStatus.FAILED
        assert t.last_error and "VM_NET_APPLY_FAILED" in t.last_error
        # best-effort доустановка libguestfs-tools была попытана
        assert any("libguestfs-tools" in c for c in fake.commands)


class TestVmSetNetworkNat:
    # Детерминированный статик-адрес NAT-гостя `xfs-1` в подсети natbr0.
    _NAT_ADDR = "192.168.100.73"

    async def test_nat_static_and_natbr0_switch(
        self, make_task, fetch_task, captured_audit, stub_session_and_callbacks,
    ):
        fake = _FakeSshClient()
        fake.set_response("virsh domstate", 0, "running")
        stub_session_and_callbacks["holder"]["ssh"] = fake
        payload = {
            "vm_id": "vm2", "hub_host": "10.0.0.7", "vm_name": "xfs-1",
            "network_mode": "nat", "guest_ip": "10.177.103.150",
            "target_department_id": "dep1",
        }
        tid = await make_task(task_kind="vm.set_network", target_server_id="hub1", payload=payload)
        await vms_network.vm_set_network.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        cmds = fake.commands
        # host-only мост natbr0 гарантирован на хабе перед переводом NIC
        assert any("dbos-vms-nat.sh" in c for c in cmds)
        # статика: детерминированный адрес подсети natbr0, шлюз/DNS 192.168.100.1
        i_static = _idx(cmds, "/etc/network/interfaces")
        assert f"address {self._NAT_ADDR}" in cmds[i_static]
        assert "netmask 255.255.255.0" in cmds[i_static]
        assert "gateway 192.168.100.1" in cmds[i_static]
        assert "dns-nameservers 192.168.100.1" in cmds[i_static]
        # NIC переведён на реальный мост natbr0 (не SLIRP user-networking)
        i_switch = _idx(cmds, "virt-xml xfs-1 --edit --network bridge=natbr0,model=virtio")
        assert i_static < i_switch
        assert not any("user,model=virtio" in c for c in cmds)
        assert any("virsh start xfs-1" in c for c in cmds)
        # NIC-свитч и рестарт домена — под root
        _assert_root_libvirt(
            fake.calls, "virt-xml xfs-1 --edit --network bridge=natbr0",
        )
        _assert_root_libvirt(fake.calls, "virsh start xfs-1")
        # applied_ip детерминирован и известен сразу (гость достижим с хаба)
        state = stub_session_and_callbacks["calls"]["vm_state"][0]
        assert state["ip_address"] == self._NAT_ADDR
        assert state["power_state"] == "on"

    async def test_nat_offline_fallback_when_guest_unreachable(
        self, make_task, fetch_task, captured_audit, stub_session_and_callbacks,
    ):
        # Текущего адреса гостя нет (domifaddr пуст, guest_ip не задан) →
        # статику заливаем offline в диск, но applied_ip всё равно
        # детерминирован (адрес natbr0 известен заранее).
        fake = _FakeSshClient()
        fake.set_response("virsh domstate", 0, "running")
        fake.set_response(
            "virsh domblklist", 0,
            " Target   Source\n"
            "---------------------------------\n"
            " vda      /vms/xfs-1.qcow2\n",
        )
        fake.set_response("command -v virt-customize", 0)
        fake.set_response("mktemp", 0, "/tmp/dbos-if")
        stub_session_and_callbacks["holder"]["ssh"] = fake
        payload = {
            "vm_id": "vm2", "hub_host": "10.0.0.7", "vm_name": "xfs-1",
            "network_mode": "nat", "target_department_id": "dep1",
        }
        tid = await make_task(task_kind="vm.set_network", target_server_id="hub1", payload=payload)
        await vms_network.vm_set_network.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        cmds = fake.commands
        # статику в госта по SSH не писали — только offline в диск
        assert not any(
            "printf" in c and "/etc/network/interfaces" in c for c in cmds
        )
        i_customize = _idx(cmds, "virt-customize -a /vms/xfs-1.qcow2 ")
        i_destroy = _idx(cmds, "virsh destroy xfs-1")
        i_start = _idx(cmds, "virsh start xfs-1")
        assert i_destroy < i_customize < i_start
        # NIC всё равно переведён на natbr0
        assert any(
            "virt-xml xfs-1 --edit --network bridge=natbr0,model=virtio" in c
            for c in cmds
        )
        state = stub_session_and_callbacks["calls"]["vm_state"][0]
        assert state["ip_address"] == self._NAT_ADDR
        assert state["power_state"] == "on"

    async def test_invalid_mode_rejected(
        self, make_task, fetch_task, captured_audit, stub_session_and_callbacks,
    ):
        fake = _FakeSshClient()
        stub_session_and_callbacks["holder"]["ssh"] = fake
        payload = {
            "vm_id": "vm2", "hub_host": "10.0.0.7", "vm_name": "xfs-1",
            "network_mode": "wan",
        }
        tid = await make_task(task_kind="vm.set_network", target_server_id="hub1", payload=payload)
        await _set_single_attempt(tid)
        await vms_network.vm_set_network.original_func(tid)
        t = await fetch_task(tid)
        assert t.status == TaskStatus.FAILED
        assert t.last_error and "VM_INVALID_ARG" in t.last_error


# ── чистые хелперы ───────────────────────────────────────────────────────────


class TestHelpers:
    def test_public_key_validator(self):
        assert _vm_prepare_helpers._validate_public_key(
            "ssh-rsa AAAAB3Nza+/x== root@h", "h",
        )
        for bad in ("ssh-rsa AAA$(x) c", "not-a-key", "ssh-rsa AAA;rm c"):
            with pytest.raises(SshError):
                _vm_prepare_helpers._validate_public_key(bad, "h")

    def test_secret_validator_rejects_metachars(self):
        assert _vm_prepare_helpers._validate_secret("Good1Pass", "h", "pw") == "Good1Pass"
        for bad in ("a'b", "a$b", "a\nb", ""):
            with pytest.raises(SshError):
                _vm_prepare_helpers._validate_secret(bad, "h", "pw")

    def test_normalize_dns(self):
        assert vms_network._normalize_dns({"dns": "8.8.8.8"}, "h") == ["8.8.8.8"]
        assert vms_network._normalize_dns(
            {"dns": ["1.1.1.1", "1.0.0.1"]}, "h",
        ) == ["1.1.1.1", "1.0.0.1"]
        # дефолт стенда, если не задано
        assert vms_network._normalize_dns({}, "h") == ["10.177.180.246"]

    def test_first_qcow2_path(self):
        out = (
            " Target   Source\n"
            "-------------------------------\n"
            " vda      /vms/station-a.qcow2\n"
            " sda      -\n"
        )
        assert vms_network._first_qcow2_path(out) == "/vms/station-a.qcow2"
        assert vms_network._first_qcow2_path(" hda   -\n") is None
        assert vms_network._first_qcow2_path("") is None
