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
from src.tasks import _vm_prepare_helpers, _vms_helpers, vms


# ── SSH mock ─────────────────────────────────────────────────────────────────


class _FakeSshClient:
    """Мок SshClient: пишет (command, stdin, sudo), отдаёт заданный ответ по
    подстроке команды. Дефолт — (0, "", "") (команда «прошла»)."""

    def __init__(self, host: str = "10.0.0.7"):
        self.host = host
        self._responses: list[tuple[str, tuple[int, str, str]]] = []
        self.commands: list[str] = []
        self.stdins: list[str | None] = []
        self.sudos: list[bool] = []

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
        self.sudos.append(sudo)
        for pat, resp in self._responses:
            if pat in command:
                return resp
        return (0, "", "")

    def sudo_for(self, pat: str) -> bool:
        """Флаг sudo первой команды, содержащей подстроку `pat`."""
        for cmd, sudo in zip(self.commands, self.sudos):
            if pat in cmd:
                return sudo
        raise AssertionError(f"нет команды с подстрокой {pat!r}")


@pytest.fixture(autouse=True)
def stub_session_and_callbacks(monkeypatch):
    """Замокать open_hub_session (отдаёт готовый fake) и оба VM-callback'а.

    Тест кладёт свой fake в `holder['ssh']` до запуска таски. Возвращает dict со
    списками вызовов callback'ов.
    """
    holder: dict = {"ssh": None}
    calls: dict = {"vm_state": [], "hub_state": [], "snapshots": [], "prepared": []}

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

    async def _vm_prepared(vm_id, management_user, target_department_id=None, **kw):
        calls["prepared"].append({"vm_id": vm_id, "management_user": management_user, "target_department_id": target_department_id, **kw})
        return {"ok": True}

    monkeypatch.setattr(vms.server_service_client, "submit_vm_state", _vm_state)
    monkeypatch.setattr(vms.server_service_client, "submit_vms_hub_state", _hub_state)
    monkeypatch.setattr(vms.server_service_client, "submit_vm_snapshots", _snapshots)
    monkeypatch.setattr(vms.server_service_client, "submit_vm_prepared", _vm_prepared)
    # Смена режима гостя ребутит его — реальное ожидание минуты, в тестах 0.
    monkeypatch.setattr(_vms_helpers, "GUEST_MODE_REBOOT_SETTLE_S", 0)
    monkeypatch.setattr(_vms_helpers, "GUEST_MODE_REBOOT_POLL_DELAY_S", 0)
    # Ожидание DHCP-lease natbr0 — в тестах 0 (иначе ретраи по 4с).
    monkeypatch.setattr(vms, "_NAT_LEASE_DELAY_S", 0)
    # Ожидание graceful-выключения перед managed-baseline снимком — в тестах 0/1.
    monkeypatch.setattr(vms, "_SNAP_OFF_POLL_DELAY_S", 0)
    monkeypatch.setattr(vms, "_SNAP_OFF_MAX_POLLS", 1)
    # Ожидание подъёма хаба после reboot — в тестах без пауз.
    monkeypatch.setattr(vms, "_HUB_REBOOT_WAIT_DELAY_S", 0)
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

    def test_parse_nat_lease_ip(self):
        line = "1700000000 52:54:00:62:ce:ef 192.168.100.42 xfs-1 01:52:54:00:62:ce:ef"
        assert vms._parse_nat_lease_ip(line) == "192.168.100.42"
        # третье поле не IPv4 / пустой вывод → None
        assert vms._parse_nat_lease_ip("garbage line here") is None
        assert vms._parse_nat_lease_ip("") is None

    def test_nat_static_ip_deterministic(self):
        # один и тот же адрес для одного имени, в диапазоне natbr0
        ip = vms._nat_static_ip("box-a")
        assert ip == vms._nat_static_ip("box-a")
        assert ip.startswith("192.168.100.")
        octet = int(ip.rsplit(".", 1)[1])
        assert 10 <= octet <= 249
        # разные имена — разные адреса (в подавляющем большинстве)
        assert vms._nat_static_ip("box-a") != vms._nat_static_ip("box-zzz")
        # никогда не совпадает с адресом самого моста
        assert ip != "192.168.100.1"


class TestVirtInstallGraphics:
    def test_default_is_vnc(self):
        cmd = vms._virt_install_cmd("v1", 2, 2048, "/vms", "bridge")
        assert "--graphics vnc,listen=0.0.0.0" in cmd
        assert "spice" not in cmd

    def test_explicit_vnc(self):
        cmd = vms._virt_install_cmd("v1", 2, 2048, "/vms", "nat", "vnc")
        assert "--graphics vnc,listen=0.0.0.0" in cmd

    def test_spice(self):
        cmd = vms._virt_install_cmd("v1", 2, 2048, "/vms", "bridge", "spice")
        assert "--graphics spice,listen=0.0.0.0" in cmd
        assert "graphics vnc" not in cmd

    def test_unknown_falls_back_to_vnc(self):
        # Неизвестный тип не должен пролезть в команду сырым (валидацию делает
        # handler, но билдер сам подстраховывается на vnc).
        cmd = vms._virt_install_cmd("v1", 2, 2048, "/vms", "bridge", "garbage")
        assert "--graphics vnc,listen=0.0.0.0" in cmd


class TestVirtInstallNetwork:
    def test_bridge_uses_native_nic_on_br0(self):
        # системный libvirt под root сам поднимает tap на мосту — штатный
        # `--network bridge=br0,model=virtio`, без qemu-commandline/helper.
        cmd = vms._virt_install_cmd("v1", 2, 2048, "/vms", "bridge")
        assert "--network bridge=br0,model=virtio" in cmd
        assert "--qemu-commandline" not in cmd
        assert "--network none" not in cmd
        assert "qemu-bridge-helper" not in cmd
        assert "--machine pc" not in cmd
        assert "addr=0x10" not in cmd

    def test_nat_uses_natbr0(self):
        # NAT-гость сидит на host-only мосту natbr0 (dnsmasq + MASQUERADE), NIC
        # цепляется тем же штатным `--network bridge` — отличается только мост.
        cmd = vms._virt_install_cmd("v1", 2, 2048, "/vms", "nat")
        assert "--network bridge=natbr0,model=virtio" in cmd
        assert "--qemu-commandline" not in cmd
        assert "qemu-bridge-helper" not in cmd
        assert "--machine pc" not in cmd
        # старый SLIRP-путь ушёл
        assert "--network user" not in cmd
        assert "network=test" not in cmd


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


def _kiosk_off(fake):
    """Проставить fake так, будто parsec-kiosk2 уже снят (не active, не enabled)."""
    fake.set_response("is-active parsec-kiosk2", 3, "inactive")
    fake.set_response("is-enabled parsec-kiosk2", 1, "disabled")
    return fake


def _prepare_fake():
    fake = _FakeSshClient()
    fake.set_response("test -e /dev/kvm", 0)
    _kiosk_off(fake)  # kiosk снят → единственный драйвер ребута тут — br0
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
    _kiosk_off(fake)  # kiosk снят → без br0-ребута хабу перезагрузка не нужна
    fake.set_response("ip link show br0", 0)  # мост уже поднят
    fake.set_response("virsh pool-info", 1)
    fake.set_response("test -f", 1)
    return fake


class _RebootDropSsh(_FakeSshClient):
    """Мок, который на reboot-триггере рвёт SSH (SshError) — как настоящий хост,
    успевший уйти в перезагрузку до закрытия сессии."""

    async def run(self, command, *, sudo=False, stdin_payload=None):
        self.commands.append(command)
        self.stdins.append(stdin_payload)
        self.sudos.append(sudo)
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
        # системная инфра: каталог хранилища root-owned (mkdir без chown).
        # session-костыли (qemu-bridge-helper/allow/linger/user-libvirtd) убраны.
        assert any("mkdir -p /vms" in c for c in cmds)
        assert not any("qemu-bridge-helper" in c for c in cmds)
        assert not any("/etc/qemu" in c for c in cmds)
        assert not any("chown -R" in c and "/vms" in c for c in cmds)
        assert not any("enable-linger" in c for c in cmds)
        assert not any("virtqemud.socket" in c for c in cmds)
        # всё идёт под root (sudo=True); libvirt-команды несут env-префикс
        assert fake.sudo_for("apt-get install -y astra-kvm") is True
        assert fake.sudo_for("mkdir -p /vms") is True
        assert fake.sudo_for("pool-define-as vms dir") is True
        assert vms.LIBVIRT_SESSION_ENV in next(
            c for c in cmds if "pool-define-as vms dir" in c
        )
        # callback prepared=True + phy_if
        assert stub_session_and_callbacks["calls"]["hub_state"] == [
            {"server_id": "hub1", "prepared": True, "target_department_id": "dep1", "phy_if": "ens192"},
        ]

    async def test_nat_bridge_setup_no_reboot(self, make_task, fetch_task, captured_audit, stub_session_and_callbacks):
        # br0 уже есть → в тесте нет reboot/guard, а natbr0 всё равно ставится.
        fake = _prepare_fake_bridge_present()
        stub_session_and_callbacks["holder"]["ssh"] = fake
        tid = await make_task(task_kind="vms_hub.prepare", target_server_id="hub1", payload=_prepare_payload("apt"))
        await vms.vms_hub_prepare.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        cmds = fake.commands
        # dnsmasq ставится пакетом хаба
        assert any("apt-get install -y" in c and "dnsmasq" in c for c in cmds)
        # oneshot-скрипт natbr0: мост + MASQUERADE в теле, unit + enable
        nat_idx = next(i for i, c in enumerate(cmds) if "dbos-vms-nat.sh" in c and "tee" in c)
        script = fake.stdins[nat_idx] or ""
        assert "ip link add natbr0 type bridge" in script
        assert "192.168.100.1/24" in script
        assert "-s 192.168.100.0/24 ! -o natbr0 -j MASQUERADE" in script
        assert "net.ipv4.ip_forward=1" in script
        assert any("dbos-vms-nat.service" in c and "tee" in c for c in cmds)
        assert any("systemctl enable dbos-vms-nat.service" in c for c in cmds)
        # скрипт исполняется на живую (host-only, без reboot)
        assert any(c.strip() == "/usr/local/sbin/dbos-vms-nat.sh" for c in cmds)
        # dnsmasq на natbr0: конфиг в dnsmasq.d + enable/restart сервиса
        dm_idx = next(i for i, c in enumerate(cmds) if "dbos-natbr0.conf" in c and "tee" in c)
        dm_conf = fake.stdins[dm_idx] or ""
        assert "interface=natbr0" in dm_conf
        assert "bind-interfaces" in dm_conf
        assert "dhcp-range=192.168.100.10,192.168.100.250,12h" in dm_conf
        assert "dhcp-leasefile=/var/lib/misc/dnsmasq.natbr0.leases" in dm_conf
        assert any("systemctl enable --now dnsmasq" in c for c in cmds)
        # qemu-bridge-helper allow-list убран — системный libvirt цепляет tap сам
        assert not any("/etc/qemu" in c for c in cmds)
        # natbr0-инфра идёт под sudo
        assert fake.sudo_for("dbos-vms-nat.sh") is True
        assert fake.sudo_for("dbos-natbr0.conf") is True
        # мост host-only → никакого reboot/guard из-за natbr0
        assert not any("systemctl reboot" in c for c in cmds)

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
        # guard ставится ДО reboot-триггера
        reboot_idx = next(i for i, c in enumerate(cmds) if "systemctl reboot" in c)
        assert guard_idx < reboot_idx
        # health-check _wait_hub_back идёт ПОСЛЕ reboot (хаб пропинган на подъёме)
        assert any(
            "test -e /dev/kvm" in c for c in cmds[reboot_idx + 1:]
        )
        # callback prepared=True ушёл ПОСЛЕ reboot (reboot-then-wait-then-report)
        assert order and order[0] > reboot_idx

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

    async def test_disable_kiosk_active_returns_true(self):
        fake = _FakeSshClient()
        fake.set_response("is-active parsec-kiosk2", 0, "active")
        fake.set_response("is-enabled parsec-kiosk2", 0, "enabled")
        result = await vms._disable_kiosk(fake, "10.0.0.7")
        assert result is True
        # kiosk снят: disable --now + mask + сброс enforce-флага
        assert any(
            "systemctl disable --now parsec-kiosk2.service" in c
            and "systemctl mask parsec-kiosk2.service" in c
            and "kiosk2_enforce" in c
            for c in fake.commands
        )
        assert fake.sudo_for("systemctl disable --now parsec-kiosk2.service") is True

    async def test_disable_kiosk_inactive_disabled_returns_false(self):
        fake = _FakeSshClient()
        fake.set_response("is-active parsec-kiosk2", 3, "inactive")
        fake.set_response("is-enabled parsec-kiosk2", 1, "disabled")
        result = await vms._disable_kiosk(fake, "10.0.0.7")
        assert result is False
        # менять нечего — ни disable, ни mask не выполнялись
        assert not any("disable --now parsec-kiosk2" in c for c in fake.commands)
        assert not any("mask parsec-kiosk2" in c for c in fake.commands)

    async def test_kiosk_change_triggers_reboot_and_waits(
        self, make_task, fetch_task, captured_audit, stub_session_and_callbacks, monkeypatch,
    ):
        # br0 уже есть (needs_reboot=False), но kiosk активен → reboot всё равно
        # нужен, а prepared=True должен уйти только после подъёма хаба.
        fake = _FakeSshClient()
        fake.set_response("test -e /dev/kvm", 0)
        fake.set_response("is-active parsec-kiosk2", 0, "active")
        fake.set_response("is-enabled parsec-kiosk2", 0, "enabled")
        fake.set_response("ip link show br0", 0)  # мост уже поднят
        fake.set_response("virsh pool-info", 1)
        fake.set_response("test -f", 1)
        stub_session_and_callbacks["holder"]["ssh"] = fake

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
        # kiosk снят
        assert any("disable --now parsec-kiosk2.service" in c for c in cmds)
        # br0 уже был → net-guard не ставили (сеть не трогали)
        assert not any("dbos-net-guard" in c for c in cmds)
        # reboot всё равно случился из-за kiosk
        reboot_idx = next(i for i, c in enumerate(cmds) if "systemctl reboot" in c)
        # health-check _wait_hub_back после reboot
        assert any("test -e /dev/kvm" in c for c in cmds[reboot_idx + 1:])
        # callback prepared=True ушёл ПОСЛЕ reboot (после подъёма хаба)
        assert order and order[0] > reboot_idx

    async def test_wait_hub_back_timeout_raises(self, monkeypatch, stub_session_and_callbacks):
        # Хаб не поднимается: health-check всегда падает → VMS_HUB_REBOOT_TIMEOUT.
        fake = _FakeSshClient()
        fake.set_response("test -e /dev/kvm", 1)  # kvm-проба не проходит
        stub_session_and_callbacks["holder"]["ssh"] = fake
        monkeypatch.setattr(vms, "_HUB_REBOOT_WAIT_ATTEMPTS", 3)

        with pytest.raises(SshError) as ei:
            await vms._wait_hub_back(_prepare_payload("apt"), "10.0.0.7")
        assert ei.value.error_code == "VMS_HUB_REBOOT_TIMEOUT"

    async def test_wait_hub_back_swallows_connect_fail_then_succeeds(
        self, monkeypatch, stub_session_and_callbacks,
    ):
        # Первые попытки — connect-фейл (хаб ещё в reboot), затем сессия встаёт.
        fake = _FakeSshClient()
        fake.set_response("test -e /dev/kvm", 0)
        calls = {"n": 0}

        async def _open(payload):  # noqa: ARG001
            calls["n"] += 1
            if calls["n"] < 3:
                raise SshError(error_code="SSH_CONNECT_FAILED", host="10.0.0.7")
            return fake, fake.host

        monkeypatch.setattr(vms, "open_hub_session", _open)
        # не должно бросить — connect-фейлы проглочены, на 3-й попытке успех
        await vms._wait_hub_back(_prepare_payload("apt"), "10.0.0.7")
        assert calls["n"] == 3


# ── vm.create ────────────────────────────────────────────────────────────────


def _create_fake():
    fake = _FakeSshClient()
    fake.set_response("test -f", 0)  # бокс в пуле
    # статику (bridge и nat) льём в диск offline через virt-customize — мокаем
    # наличие тула и mktemp под временный /etc/network/interfaces.
    fake.set_response("command -v virt-customize", 0)
    fake.set_response("mktemp", 0, "/tmp/dbos-if")
    fake.set_response("virsh domstate", 0, "running")
    return fake


class TestVmCreateUniversal:
    async def test_universal_builds_golden_orel_smolensk(self, make_task, fetch_task, captured_audit, stub_session_and_callbacks):
        fake = _create_fake()
        fake.set_response("command -v virt-customize", 0)  # libguestfs стоит
        fake.set_response("mktemp", 0, "/tmp/dbos-if")
        stub_session_and_callbacks["holder"]["ssh"] = fake
        payload = {
            "vm_id": "vm1", "hub_host": "10.0.0.7", "name": "station-a",
            "cpu": 16, "ram_mb": 131072, "disk_gb": 0, "box": "vm_station",
            "network_mode": "bridge", "ip_address": "10.177.103.101",
            "gateway": "10.177.103.254", "netmask": "255.255.255.0",
            "dns": ["10.177.180.246"],
            "os_versions": ["1.7.5.9"], "storage_pool_path": "/vms",
            "is_managed": True, "target_department_id": "dep1",
        }
        tid = await make_task(task_kind="vm.create", target_server_id="hub1", payload=payload)
        await vms.vm_create.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        cmds = fake.commands
        # universal всегда cp (virt-resize снёс бы внутренние qemu-снимки версий)
        assert any("cp /vms/vm_station.qcow2 /vms/station-a.qcow2" in c for c in cmds)
        assert not any("virt-resize" in c for c in cmds)
        # домен собирается на br0 штатным NIC (не на NAT natbr0)
        assert any("virt-install -n station-a" in c and "--network bridge=br0,model=virtio" in c for c in cmds)
        assert not any("virt-install -n station-a" in c and "natbr0" in c for c in cmds)
        # весь VM/диск-флоу идёт под root (sudo=True), с env-префиксом
        assert fake.sudo_for("virt-install -n station-a") is True
        assert fake.sudo_for("cp /vms/vm_station.qcow2") is True
        assert fake.sudo_for("qemu-img snapshot -a 1.7.5.9") is True
        assert vms.LIBVIRT_SESSION_ENV in next(
            c for c in cmds if "virt-install -n station-a" in c
        )
        assert any("qemu-img snapshot -a 1.7.5.9 /vms/station-a.qcow2" in c for c in cmds)
        # порядок: переключили версию → залили статику offline → подняли ВМ
        i_disk = next(i for i, c in enumerate(cmds) if "qemu-img snapshot -a 1.7.5.9" in c)
        i_customize = next(
            i for i, c in enumerate(cmds)
            if "virt-customize -a /vms/station-a.qcow2 " in c
            and "--upload /tmp/dbos-if:/etc/network/interfaces" in c
        )
        i_start = next(i for i, c in enumerate(cmds) if "virsh start station-a" in c)
        assert i_disk < i_customize < i_start
        # NIC уже на мосту — отдельного virt-xml-переключения нет
        assert not any("virt-xml station-a --edit --network" in c for c in cmds)
        # провижн идёт по боевой статике, а не по domifaddr NAT-lease
        assert not any("domifaddr station-a" in c for c in cmds)
        # статик-конфиг несёт боевой адрес из пула
        tee_idx = next(i for i, c in enumerate(cmds) if "tee /tmp/dbos-if" in c)
        body = fake.stdins[tee_idx] or ""
        assert "address 10.177.103.101" in body
        assert "gateway 10.177.103.254" in body
        # golden (скрытый) + Орёл + Смоленск
        assert any("snapshot-create-as station-a --name 1.7.5.9_build" in c for c in cmds)
        assert any("snapshot-create-as station-a --name 1.7.5.9_orel" in c for c in cmds)
        assert any("snapshot-create-as station-a --name 1.7.5.9_smolensk" in c for c in cmds)
        # смена режима на Смоленск: modeswitch + МРД + МКЦ (и порядок до снимка)
        assert any("astra-modeswitch set 2" in c for c in cmds)
        assert any("astra-mac-control enable" in c for c in cmds)
        assert any("astra-mic-control enable" in c for c in cmds)
        i_switch = next(i for i, c in enumerate(cmds) if "astra-modeswitch set 2" in c)
        i_smol = next(i for i, c in enumerate(cmds) if "--name 1.7.5.9_smolensk" in c)
        assert i_switch < i_smol
        # rich snapshot-callback: golden(is_system) + orel + smolensk c mode/kind/os_version
        snaps = stub_session_and_callbacks["calls"]["snapshots"][0]["snapshots"]
        by_name = {s["name"]: s for s in snaps}
        assert by_name["1.7.5.9_build"]["is_system"] is True
        assert by_name["1.7.5.9_build"]["mode"] == "orel"
        assert by_name["1.7.5.9_orel"]["mode"] == "orel"
        assert by_name["1.7.5.9_orel"]["kind"] == "os_baseline"
        assert by_name["1.7.5.9_orel"]["os_version"] == "1.7.5.9"
        assert by_name["1.7.5.9_smolensk"]["mode"] == "smolensk"
        # state-callback: plain-снимки без скрытого golden
        state = stub_session_and_callbacks["calls"]["vm_state"][0]
        assert state["snapshots"] == ["1.7.5.9_orel", "1.7.5.9_smolensk"]
        assert state["power_state"] == "on"
        assert state["status"] == "free"
        assert state["ip_address"] == "10.177.103.101"

    async def test_universal_two_versions_inject_static_each(self, make_task, fetch_task, captured_audit, stub_session_and_callbacks):
        fake = _create_fake()
        fake.set_response("command -v virt-customize", 0)
        fake.set_response("mktemp", 0, "/tmp/dbos-if")
        stub_session_and_callbacks["holder"]["ssh"] = fake
        payload = {
            "vm_id": "vm1", "hub_host": "10.0.0.7", "name": "station-a",
            "cpu": 4, "ram_mb": 4096, "disk_gb": 0, "box": "vm_station",
            "network_mode": "bridge", "ip_address": "10.177.103.110",
            "os_versions": ["1.7.5.9", "1.8.1.6"], "storage_pool_path": "/vms",
            "is_managed": True, "target_department_id": "dep1",
        }
        tid = await make_task(task_kind="vm.create", target_server_id="hub1", payload=payload)
        await vms.vm_create.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        cmds = fake.commands
        # каждая версия: свой qemu-snapshot -a + своя offline-инъекция статики
        assert any("qemu-img snapshot -a 1.7.5.9 /vms/station-a.qcow2" in c for c in cmds)
        assert any("qemu-img snapshot -a 1.8.1.6 /vms/station-a.qcow2" in c for c in cmds)
        n_customize = sum(
            1 for c in cmds
            if "virt-customize -a /vms/station-a.qcow2 " in c
            and "--upload /tmp/dbos-if:/etc/network/interfaces" in c
        )
        assert n_customize == 2
        # обе версии дают golden + orel + smolensk
        for ver in ("1.7.5.9", "1.8.1.6"):
            for suf in ("_build", "_orel", "_smolensk"):
                assert any(f"snapshot-create-as station-a --name {ver}{suf}" in c for c in cmds)
        state = stub_session_and_callbacks["calls"]["vm_state"][0]
        assert state["snapshots"] == [
            "1.7.5.9_orel", "1.7.5.9_smolensk",
            "1.8.1.6_orel", "1.8.1.6_smolensk",
        ]

    async def test_universal_requires_ip_address(self, make_task, fetch_task, captured_audit, stub_session_and_callbacks):
        fake = _create_fake()
        stub_session_and_callbacks["holder"]["ssh"] = fake
        payload = {
            "vm_id": "vm1", "hub_host": "10.0.0.7", "name": "station-a",
            "cpu": 4, "ram_mb": 4096, "box": "vm_station",
            "network_mode": "bridge", "ip_address": None,
            "os_versions": ["1.7.5.9"], "is_managed": True,
        }
        tid = await make_task(task_kind="vm.create", target_server_id="hub1", payload=payload)
        await _set_single_attempt(tid)
        await vms.vm_create.original_func(tid)
        t = await fetch_task(tid)
        assert t.status == TaskStatus.FAILED
        assert t.last_error and "VM_INVALID_ARG" in t.last_error

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
        assert any("--network bridge=natbr0,model=virtio" in c for c in cmds)  # nat → host-only natbr0
        assert not any("--network user" in c for c in cmds)
        assert not any("network=test" in c for c in cmds)
        # nat-гость на детерминированной статике в подсети natbr0 — lease
        # больше не читаем (grep leasefile отсутствует)
        assert not any("dnsmasq.natbr0.leases" in c for c in cmds)
        nat_ip = vms._nat_static_ip("xfs-1")
        # статику NAT льём в диск offline через virt-customize (как bridge)
        assert any(
            "virt-customize -a /vms/xfs-1.qcow2 " in c
            and "--upload /tmp/dbos-if:/etc/network/interfaces" in c
            for c in cmds
        )
        # virt-install идёт под root (sudo=True) с env-префиксом
        assert vms.LIBVIRT_SESSION_ENV in next(
            c for c in cmds if "virt-install -n xfs-1" in c
        )
        assert fake.sudo_for("virt-install -n xfs-1") is True
        # single-бокс с известной версией: golden + Орёл + Смоленск (как universal)
        assert any("snapshot-create-as xfs-1 --name 1.8.1.6_build" in c for c in cmds)
        assert any("snapshot-create-as xfs-1 --name 1.8.1.6_orel" in c for c in cmds)
        assert any("snapshot-create-as xfs-1 --name 1.8.1.6_smolensk" in c for c in cmds)
        state = stub_session_and_callbacks["calls"]["vm_state"][0]
        assert state["snapshots"] == ["1.8.1.6_orel", "1.8.1.6_smolensk"]
        # NAT-адрес (детерминированная статика) докладывается в state
        assert state["ip_address"] == nat_ip
        snaps = stub_session_and_callbacks["calls"]["snapshots"][0]["snapshots"]
        by_name = {s["name"]: s for s in snaps}
        assert by_name["1.8.1.6_build"]["is_system"] is True
        assert by_name["1.8.1.6_orel"]["os_version"] == "1.8.1.6"
        assert by_name["1.8.1.6_smolensk"]["mode"] == "smolensk"

    async def test_single_nat_ensures_bridge_before_final_start(self, make_task, fetch_task, captured_audit, stub_session_and_callbacks):
        # NAT single: natbr0 гарантируется перед финальным подъёмом ВМ, чтобы
        # tap прицепился к мосту (иначе гость без сети).
        fake = _create_fake()
        stub_session_and_callbacks["holder"]["ssh"] = fake
        payload = {
            "vm_id": "vm2", "hub_host": "10.0.0.7", "name": "xfs-1",
            "cpu": 4, "ram_mb": 4096, "disk_gb": 0, "box": "xfs.box",
            "network_mode": "nat", "ip_address": None, "os_versions": [],
            "os_version": "1.8.1.6", "storage_pool_path": "/vms", "is_managed": True,
        }
        tid = await make_task(task_kind="vm.create", target_server_id="hub1", payload=payload)
        await vms.vm_create.original_func(tid)
        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        cmds = fake.commands
        i_setup = next(i for i, c in enumerate(cmds) if "dbos-vms-nat.sh" in c)
        i_start = next(i for i, c in enumerate(cmds) if "virsh start xfs-1" in c)
        assert i_setup < i_start

    async def test_single_bridge_skips_bridge_setup(self, make_task, fetch_task, captured_audit, stub_session_and_callbacks):
        # bridge single на br0 — natbr0 не поднимаем.
        fake = _create_fake()
        stub_session_and_callbacks["holder"]["ssh"] = fake
        payload = {
            "vm_id": "vm2", "hub_host": "10.0.0.7", "name": "xfs-1",
            "cpu": 4, "ram_mb": 4096, "disk_gb": 0, "box": "xfs.box",
            "network_mode": "bridge", "ip_address": "10.177.103.50/24",
            "os_versions": [], "os_version": "1.8.1.6",
            "storage_pool_path": "/vms", "is_managed": True,
        }
        tid = await make_task(task_kind="vm.create", target_server_id="hub1", payload=payload)
        await vms.vm_create.original_func(tid)
        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        assert not any("dbos-vms-nat.sh" in c for c in fake.commands)

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
        # (`command -v virt-resize` — лишь проба наличия тула, не работа).
        assert not any("shrink-src" in c for c in cmds)
        assert not any("resize2fs-size" in c for c in cmds)
        assert not any("virt-resize --" in c for c in cmds)

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


def _mgmt_material():
    return {
        "management_user": "dbos",
        "public_key": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAA dbos@hub",
        "private_key": (
            "-----BEGIN OPENSSH PRIVATE KEY-----\nZmFrZQ==\n"
            "-----END OPENSSH PRIVATE KEY-----\n"
        ),
        "password": "Str0ngMgmtPass",
    }


def _managed_load(monkeypatch):
    """Замокать чтение управляющего материала из stash фиксированным набором."""
    async def _fake(stash_key, host):  # noqa: ARG001
        return _mgmt_material()
    monkeypatch.setattr(vms, "load_mgmt_material", _fake)


class TestVmCreateManaged:
    """prepare встроен в сборку: с `creds_stash_key` каждая версия становится
    managed (dbos-учётка, `u` снесён), доступ переключается на ключ."""

    async def test_universal_managed_baseline_then_key_access(
        self, make_task, fetch_task, captured_audit, stub_session_and_callbacks, monkeypatch,
    ):
        _managed_load(monkeypatch)
        fake = _create_fake()
        stub_session_and_callbacks["holder"]["ssh"] = fake
        payload = {
            "vm_id": "vm1", "hub_host": "10.0.0.7", "name": "station-a",
            "cpu": 4, "ram_mb": 4096, "disk_gb": 0, "box": "vm_station",
            "network_mode": "bridge", "ip_address": "10.177.103.101",
            "os_versions": ["1.7.5.9"], "storage_pool_path": "/vms",
            "is_managed": True, "target_department_id": "dep1",
            "creds_stash_key": "dbos:dispatch_creds:dcd_abc",
        }
        tid = await make_task(task_kind="vm.create", target_server_id="hub1", payload=payload)
        await vms.vm_create.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        cmds = fake.commands
        # bootstrap управления: заведён dbos-юзер + снесена базовая учётка `u`
        assert any("useradd -m -s /bin/bash dbos" in c for c in cmds)
        assert any("userdel -rf u" in c for c in cmds)
        # sudoers NOPASSWD для dbos
        assert any("dbos ALL=(ALL) NOPASSWD: ALL" in c for c in cmds)
        # снос `u` идёт по управляющему ключу (ssh -i), не по паролю
        userdel = next(c for c in cmds if "userdel -rf u" in c)
        assert "ssh -i /tmp/dbos-if" in userdel
        assert "dbos@10.177.103.101" in userdel
        # перевод в Смоленск — уже по ключу (u снесён)
        smol = next(c for c in cmds if "astra-modeswitch set 2" in c)
        assert "ssh -i /tmp/dbos-if" in smol
        # чистый managed-baseline снят на ВЫКЛЮЧЕННОЙ ВМ: shutdown → snapshot → start
        i_shutdown = next(i for i, c in enumerate(cmds) if "virsh shutdown station-a" in c)
        i_build = next(i for i, c in enumerate(cmds) if "snapshot-create-as station-a --name 1.7.5.9_build" in c)
        i_start_after = next(i for i, c in enumerate(cmds) if i > i_build and "virsh start station-a" in c)
        assert i_shutdown < i_build < i_start_after
        # snapshots: golden(is_system) + orel + smolensk
        assert any("snapshot-create-as station-a --name 1.7.5.9_orel" in c for c in cmds)
        assert any("snapshot-create-as station-a --name 1.7.5.9_smolensk" in c for c in cmds)
        # callback managed: ВМ помечена управляемой с management_user
        prepared = stub_session_and_callbacks["calls"]["prepared"]
        assert prepared and prepared[0]["management_user"] == "dbos"
        state = stub_session_and_callbacks["calls"]["vm_state"][0]
        assert state["snapshots"] == ["1.7.5.9_orel", "1.7.5.9_smolensk"]

    async def test_universal_managed_accounts_after_baseline_by_key(
        self, make_task, fetch_task, captured_audit, stub_session_and_callbacks, monkeypatch,
    ):
        _managed_load(monkeypatch)
        fake = _create_fake()
        stub_session_and_callbacks["holder"]["ssh"] = fake
        payload = {
            "vm_id": "vm1", "hub_host": "10.0.0.7", "name": "station-a",
            "cpu": 4, "ram_mb": 4096, "disk_gb": 0, "box": "vm_station",
            "network_mode": "bridge", "ip_address": "10.177.103.101",
            "os_versions": ["1.7.5.9"], "storage_pool_path": "/vms",
            "is_managed": True, "creds_stash_key": "dbos:dispatch_creds:dcd_abc",
            "accounts": [{"account_id": "acc1", "login": "deploy", "password": "Deploy1", "has_sudo": False, "unix_groups": []}],
        }
        tid = await make_task(task_kind="vm.create", target_server_id="hub1", payload=payload)
        await vms.vm_create.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        cmds = fake.commands
        # привязанный юзер заводится ПОСЛЕ чистого baseline и по ключу (ssh -i)
        useradd_deploy = next(c for c in cmds if "useradd -m deploy" in c)
        assert "ssh -i /tmp/dbos-if" in useradd_deploy
        i_build = next(i for i, c in enumerate(cmds) if "--name 1.7.5.9_build" in c)
        i_deploy = next(i for i, c in enumerate(cmds) if "useradd -m deploy" in c)
        i_orel = next(i for i, c in enumerate(cmds) if "--name 1.7.5.9_orel" in c)
        assert i_build < i_deploy < i_orel

    async def test_single_managed_build_deletes_base_user(
        self, make_task, fetch_task, captured_audit, stub_session_and_callbacks, monkeypatch,
    ):
        _managed_load(monkeypatch)
        fake = _create_fake()
        stub_session_and_callbacks["holder"]["ssh"] = fake
        payload = {
            "vm_id": "vm2", "hub_host": "10.0.0.7", "name": "single-1",
            "cpu": 4, "ram_mb": 4096, "disk_gb": 0, "box": "single-box",
            "network_mode": "nat", "ip_address": None, "os_versions": [],
            "os_version": "1.8.1.6", "storage_pool_path": "/vms",
            "is_managed": True, "creds_stash_key": "dbos:dispatch_creds:dcd_xyz",
        }
        tid = await make_task(task_kind="vm.create", target_server_id="hub1", payload=payload)
        await vms.vm_create.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        cmds = fake.commands
        assert any("useradd -m -s /bin/bash dbos" in c for c in cmds)
        assert any("userdel -rf u" in c for c in cmds)
        # single-бокс с известной версией: golden `<ver>_build` + Орёл + Смоленск
        assert any("snapshot-create-as single-1 --name 1.8.1.6_build" in c for c in cmds)
        assert any("snapshot-create-as single-1 --name 1.8.1.6_orel" in c for c in cmds)
        assert any("snapshot-create-as single-1 --name 1.8.1.6_smolensk" in c for c in cmds)
        # golden снят на ВЫКЛЮЧЕННОЙ ВМ: shutdown → snapshot → старт под Орёл
        i_shutdown = next(i for i, c in enumerate(cmds) if "virsh shutdown single-1" in c)
        i_build = next(i for i, c in enumerate(cmds) if "snapshot-create-as single-1 --name 1.8.1.6_build" in c)
        i_start_after = next(i for i, c in enumerate(cmds) if i > i_build and "virsh start single-1" in c)
        assert i_shutdown < i_build < i_start_after
        prepared = stub_session_and_callbacks["calls"]["prepared"]
        assert prepared and prepared[0]["management_user"] == "dbos"

    async def test_legacy_path_when_no_stash_key(
        self, make_task, fetch_task, captured_audit, stub_session_and_callbacks,
    ):
        """Без `creds_stash_key` — прежнее поведение по `u`/`1`, без сноса `u`."""
        fake = _create_fake()
        stub_session_and_callbacks["holder"]["ssh"] = fake
        payload = {
            "vm_id": "vm3", "hub_host": "10.0.0.7", "name": "single-2",
            "cpu": 4, "ram_mb": 4096, "disk_gb": 0, "box": "single-box",
            "network_mode": "nat", "ip_address": None, "os_versions": [],
            "os_version": "1.8.1.6", "storage_pool_path": "/vms", "is_managed": True,
        }
        tid = await make_task(task_kind="vm.create", target_server_id="hub1", payload=payload)
        await vms.vm_create.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        cmds = fake.commands
        assert not any("userdel -rf u" in c for c in cmds)
        assert not any("useradd -m -s /bin/bash dbos" in c for c in cmds)
        # legacy с известной версией тоже даёт golden + Орёл + Смоленск (по u/1)
        assert any("snapshot-create-as single-2 --name 1.8.1.6_build" in c for c in cmds)
        assert any("snapshot-create-as single-2 --name 1.8.1.6_orel" in c for c in cmds)
        # golden снят на ВЫКЛЮЧЕННОЙ ВМ, старт под Орёл — после
        i_shutdown = next(i for i, c in enumerate(cmds) if "virsh shutdown single-2" in c)
        i_build = next(i for i, c in enumerate(cmds) if "snapshot-create-as single-2 --name 1.8.1.6_build" in c)
        i_start_after = next(i for i, c in enumerate(cmds) if i > i_build and "virsh start single-2" in c)
        assert i_shutdown < i_build < i_start_after
        # managed-callback не вызывается в legacy-пути
        assert stub_session_and_callbacks["calls"]["prepared"] == []


class TestVmCreateBuildDeps:
    """build-зависимости CPython ставятся на доставляемую ВМ после снимков."""

    async def test_managed_installs_build_deps_by_key_after_snapshots(
        self, make_task, fetch_task, captured_audit, stub_session_and_callbacks, monkeypatch,
    ):
        _managed_load(monkeypatch)
        fake = _create_fake()
        stub_session_and_callbacks["holder"]["ssh"] = fake
        payload = {
            "vm_id": "vm1", "hub_host": "10.0.0.7", "name": "station-a",
            "cpu": 4, "ram_mb": 4096, "disk_gb": 0, "box": "vm_station",
            "network_mode": "bridge", "ip_address": "10.177.103.101",
            "os_versions": ["1.7.5.9"], "storage_pool_path": "/vms",
            "is_managed": True, "creds_stash_key": "dbos:dispatch_creds:dcd_abc",
        }
        tid = await make_task(task_kind="vm.create", target_server_id="hub1", payload=payload)
        await vms.vm_create.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        cmds = fake.commands
        deps = next(c for c in cmds if "apt-get install -y build-essential" in c)
        # ставятся по управляющему ключу (базовая учётка снесена)
        assert "ssh -i /tmp/dbos-if" in deps
        assert "dbos@10.177.103.101" in deps
        # фиксированный набор dev-пакетов присутствует, но CPython не собираем
        for pkg in ("libssl-dev", "libffi-dev", "libsqlite3-dev", "liblzma-dev"):
            assert pkg in deps
        assert "configure" not in deps
        assert "altinstall" not in deps
        # ставим ПОСЛЕ последнего снимка — эталонные снимки чистые
        i_smol = next(i for i, c in enumerate(cmds) if "--name 1.7.5.9_smolensk" in c)
        i_deps = next(i for i, c in enumerate(cmds) if "apt-get install -y build-essential" in c)
        assert i_smol < i_deps

    async def test_flag_false_skips_build_deps(
        self, make_task, fetch_task, captured_audit, stub_session_and_callbacks, monkeypatch,
    ):
        _managed_load(monkeypatch)
        fake = _create_fake()
        stub_session_and_callbacks["holder"]["ssh"] = fake
        payload = {
            "vm_id": "vm1", "hub_host": "10.0.0.7", "name": "station-a",
            "cpu": 4, "ram_mb": 4096, "disk_gb": 0, "box": "vm_station",
            "network_mode": "bridge", "ip_address": "10.177.103.101",
            "os_versions": ["1.7.5.9"], "storage_pool_path": "/vms",
            "is_managed": True, "creds_stash_key": "dbos:dispatch_creds:dcd_abc",
            "install_build_deps": False,
        }
        tid = await make_task(task_kind="vm.create", target_server_id="hub1", payload=payload)
        await vms.vm_create.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        assert not any("apt-get install -y build-essential" in c for c in fake.commands)

    async def test_legacy_installs_build_deps_by_password(
        self, make_task, fetch_task, captured_audit, stub_session_and_callbacks,
    ):
        """Legacy-путь (без stash) ставит deps по базовой учётке (sshpass), не по ключу."""
        fake = _create_fake()
        stub_session_and_callbacks["holder"]["ssh"] = fake
        payload = {
            "vm_id": "vm3", "hub_host": "10.0.0.7", "name": "single-2",
            "cpu": 4, "ram_mb": 4096, "disk_gb": 0, "box": "single-box",
            "network_mode": "nat", "ip_address": None, "os_versions": [],
            "os_version": "1.8.1.6", "storage_pool_path": "/vms",
        }
        tid = await make_task(task_kind="vm.create", target_server_id="hub1", payload=payload)
        await vms.vm_create.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        deps = next(c for c in fake.commands if "apt-get install -y build-essential" in c)
        assert "sshpass" in deps
        assert "ssh -i /tmp/dbos-if" not in deps


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

    @pytest.mark.parametrize("action", ["start", "reboot", "reset"])
    async def test_nat_power_on_ensures_bridge(self, action, make_task, fetch_task, captured_audit, stub_session_and_callbacks):
        # NAT-ВМ: natbr0 поднимается до virsh перед стартом/ребутом/резетом.
        fake = _FakeSshClient()
        fake.set_response("virsh domstate", 0, "running")
        stub_session_and_callbacks["holder"]["ssh"] = fake
        payload = {
            "vm_id": "vm9", "hub_host": "10.0.0.7", "vm_name": "station-a",
            "action": action, "network_mode": "nat", "is_managed": True,
        }
        tid = await make_task(task_kind="vm.power", target_server_id="hub1", payload=payload)
        await vms.vm_power.original_func(tid)
        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        setup_i = next(i for i, c in enumerate(fake.commands) if "dbos-vms-nat.sh" in c)
        power_i = next(i for i, c in enumerate(fake.commands) if f"virsh {action} station-a" in c)
        assert setup_i < power_i

    async def test_bridge_power_on_skips_bridge_setup(self, make_task, fetch_task, captured_audit, stub_session_and_callbacks):
        # bridge-ВМ на br0 — natbr0 не трогаем.
        fake = _FakeSshClient()
        fake.set_response("virsh domstate", 0, "running")
        stub_session_and_callbacks["holder"]["ssh"] = fake
        payload = {
            "vm_id": "vm9", "hub_host": "10.0.0.7", "vm_name": "station-a",
            "action": "start", "network_mode": "bridge", "is_managed": True,
        }
        tid = await make_task(task_kind="vm.power", target_server_id="hub1", payload=payload)
        await vms.vm_power.original_func(tid)
        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        assert not any("dbos-vms-nat.sh" in c for c in fake.commands)

    @pytest.mark.parametrize("action", ["shutdown", "destroy"])
    async def test_nat_power_off_skips_bridge_setup(self, action, make_task, fetch_task, captured_audit, stub_session_and_callbacks):
        # выключение NAT-ВМ мост не поднимает — незачем.
        fake = _FakeSshClient()
        fake.set_response("virsh domstate", 0, "shut off")
        stub_session_and_callbacks["holder"]["ssh"] = fake
        payload = {
            "vm_id": "vm9", "hub_host": "10.0.0.7", "vm_name": "station-a",
            "action": action, "network_mode": "nat", "is_managed": True,
        }
        tid = await make_task(task_kind="vm.power", target_server_id="hub1", payload=payload)
        await vms.vm_power.original_func(tid)
        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        assert not any("dbos-vms-nat.sh" in c for c in fake.commands)


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


class TestVmCreateGraphics:
    async def test_create_default_graphics_vnc(self, make_task, fetch_task, captured_audit, stub_session_and_callbacks):
        fake = _create_fake()
        stub_session_and_callbacks["holder"]["ssh"] = fake
        payload = {
            "vm_id": "vmg1", "hub_host": "10.0.0.7", "name": "gfx-1",
            "cpu": 2, "ram_mb": 2048, "disk_gb": 0, "box": "single-box",
            "network_mode": "nat", "ip_address": None, "os_versions": [],
            "storage_pool_path": "/vms", "is_managed": True,
        }
        tid = await make_task(task_kind="vm.create", target_server_id="hub1", payload=payload)
        await vms.vm_create.original_func(tid)
        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        install = next(c for c in fake.commands if "virt-install -n gfx-1" in c)
        assert "--graphics vnc,listen=0.0.0.0" in install
        assert "spice" not in install

    async def test_create_graphics_spice(self, make_task, fetch_task, captured_audit, stub_session_and_callbacks):
        fake = _create_fake()
        stub_session_and_callbacks["holder"]["ssh"] = fake
        payload = {
            "vm_id": "vmg2", "hub_host": "10.0.0.7", "name": "gfx-2",
            "cpu": 2, "ram_mb": 2048, "disk_gb": 0, "box": "single-box",
            "network_mode": "nat", "ip_address": None, "os_versions": [],
            "storage_pool_path": "/vms", "is_managed": True, "graphics": "spice",
        }
        tid = await make_task(task_kind="vm.create", target_server_id="hub1", payload=payload)
        await vms.vm_create.original_func(tid)
        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        install = next(c for c in fake.commands if "virt-install -n gfx-2" in c)
        assert "--graphics spice,listen=0.0.0.0" in install

    async def test_create_invalid_graphics_rejected(self, make_task, fetch_task, captured_audit, stub_session_and_callbacks):
        fake = _create_fake()
        stub_session_and_callbacks["holder"]["ssh"] = fake
        payload = {
            "vm_id": "vmg3", "hub_host": "10.0.0.7", "name": "gfx-3",
            "cpu": 2, "ram_mb": 2048, "disk_gb": 0, "box": "single-box",
            "network_mode": "nat", "ip_address": None, "os_versions": [],
            "storage_pool_path": "/vms", "is_managed": True, "graphics": "webrtc",
        }
        tid = await make_task(task_kind="vm.create", target_server_id="hub1", payload=payload)
        await _set_single_attempt(tid)
        await vms.vm_create.original_func(tid)
        t = await fetch_task(tid)
        assert t.status == TaskStatus.FAILED
        assert t.last_error and "VM_INVALID_ARG" in t.last_error


class TestVmListPackages:
    def _stub_packages(self, monkeypatch):
        calls: list = []

        async def _record(vm_id, packages, target_department_id=None, **kw):
            calls.append({"vm_id": vm_id, "packages": packages, "target_department_id": target_department_id, **kw})
            return {"ok": True}

        monkeypatch.setattr(vms.server_service_client, "record_vm_packages", _record)
        return calls

    async def test_dpkg_via_os_family_hint(self, make_task, fetch_task, captured_audit, stub_session_and_callbacks, monkeypatch):
        calls = self._stub_packages(monkeypatch)
        fake = _FakeSshClient()
        fake.set_response("dpkg-query -W", 0, "htop 3.0.5-7\nvim 9.0\n")
        stub_session_and_callbacks["holder"]["ssh"] = fake
        payload = {
            "vm_id": "vmp1", "hub_host": "10.0.0.7", "vm_name": "station-a",
            "guest_ip": "192.168.100.24", "os_family": "apt", "pattern": "*",
            "is_managed": True, "target_department_id": "dep1",
        }
        tid = await make_task(task_kind="vm.list_packages", target_server_id="hub1", payload=payload)
        await vms.vm_list_packages.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        # os_family=apt → детект не гоняется
        assert not any("command -v dpkg-query" in c for c in fake.commands)
        assert any("dpkg-query -W" in c for c in fake.commands)
        assert t.result["package_manager"] == "dpkg"
        assert t.result["count"] == 2
        assert t.result["packages"] == [
            {"name": "htop", "version": "3.0.5-7"},
            {"name": "vim", "version": "9.0"},
        ]
        assert calls[0]["packages"] == t.result["packages"]
        assert calls[0]["source"] == "dpkg"
        assert calls[0]["task_id"] == tid
        assert calls[0]["target_department_id"] == "dep1"
        # packages не утекают в audit-details
        details = captured_audit[0]["details"].get("result", {})
        assert "packages" not in details
        assert details.get("count") == 2

    async def test_managed_lists_via_mgmt_key(self, make_task, fetch_task, captured_audit, stub_session_and_callbacks, monkeypatch):
        # managed-ВМ (`creds_stash_key`): в гостя ходим по управляющему ключу
        # (базовая учётка снесена), а не по паролю `u`/`1`. Пакетный путь тянет
        # ключ через `load_guest_key` → `load_mgmt_material` из _vm_prepare_helpers.
        async def _fake_load(stash_key, host):  # noqa: ARG001
            return _mgmt_material()
        monkeypatch.setattr(_vm_prepare_helpers, "load_mgmt_material", _fake_load)
        self._stub_packages(monkeypatch)
        fake = _FakeSshClient()
        fake.set_response("mktemp", 0, "/tmp/dbos-mgmt-key")
        fake.set_response("dpkg-query -W", 0, "htop 3.0.5-7\n")
        stub_session_and_callbacks["holder"]["ssh"] = fake
        payload = {
            "vm_id": "vmp7", "hub_host": "10.0.0.7", "vm_name": "station-a",
            "guest_ip": "192.168.100.24", "os_family": "apt", "pattern": "*",
            "is_managed": True, "creds_stash_key": "dbos:dispatch_creds:dcd_pkg",
        }
        tid = await make_task(task_kind="vm.list_packages", target_server_id="hub1", payload=payload)
        await vms.vm_list_packages.original_func(tid)
        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        # листинг ушёл по ключу управляющего пользователя, не по sshpass
        query = next(c for c in fake.commands if "dpkg-query -W" in c)
        assert "ssh -i /tmp/dbos-mgmt-key" in query
        assert "dbos@192.168.100.24" in query
        assert "sshpass" not in query
        # временный ключ затёрт
        assert any("shred -u /tmp/dbos-mgmt-key" in c for c in fake.commands)

    async def test_detect_rpm_when_no_os_family(self, make_task, fetch_task, captured_audit, stub_session_and_callbacks, monkeypatch):
        self._stub_packages(monkeypatch)
        fake = _FakeSshClient()
        fake.set_response("command -v dpkg-query", 1)  # нет dpkg в госте
        fake.set_response("command -v rpm", 0)
        fake.set_response("rpm -qa", 0, "bash 5.2.15\n")
        stub_session_and_callbacks["holder"]["ssh"] = fake
        payload = {
            "vm_id": "vmp2", "hub_host": "10.0.0.7", "vm_name": "station-a",
            "guest_ip": "192.168.100.24", "pattern": "*", "is_managed": True,
        }
        tid = await make_task(task_kind="vm.list_packages", target_server_id="hub1", payload=payload)
        await vms.vm_list_packages.original_func(tid)
        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        assert t.result["package_manager"] == "rpm"
        assert t.result["packages"] == [{"name": "bash", "version": "5.2.15"}]

    async def test_guest_ip_from_domifaddr_when_absent(self, make_task, fetch_task, captured_audit, stub_session_and_callbacks, monkeypatch):
        self._stub_packages(monkeypatch)
        fake = _FakeSshClient()
        fake.set_response("virsh domifaddr", 0, " vnet0 52:54:00:aa:bb:cc ipv4 192.168.100.55/24")
        fake.set_response("dpkg-query -W", 0, "htop 3.0.5-7\n")
        stub_session_and_callbacks["holder"]["ssh"] = fake
        payload = {
            "vm_id": "vmp3", "hub_host": "10.0.0.7", "vm_name": "station-a",
            "os_family": "dpkg", "pattern": "htop", "is_managed": True,
        }
        tid = await make_task(task_kind="vm.list_packages", target_server_id="hub1", payload=payload)
        await vms.vm_list_packages.original_func(tid)
        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        assert any("virsh domifaddr station-a" in c for c in fake.commands)
        # guest_ssh к вычисленному адресу
        assert any("192.168.100.55" in c for c in fake.commands)
        assert t.result["count"] == 1

    async def test_no_package_manager_fails(self, make_task, fetch_task, captured_audit, stub_session_and_callbacks, monkeypatch):
        self._stub_packages(monkeypatch)
        fake = _FakeSshClient()
        # все probe'ы command -v возвращают дефолт (0,"","") — надо явно завалить
        for probe in ("dpkg-query", "rpm", "apk", "pacman", "qlist", "xbps-query"):
            fake.set_response(f"command -v {probe}", 1)
        stub_session_and_callbacks["holder"]["ssh"] = fake
        payload = {
            "vm_id": "vmp4", "hub_host": "10.0.0.7", "vm_name": "station-a",
            "guest_ip": "192.168.100.24", "pattern": "*", "is_managed": True,
        }
        tid = await make_task(task_kind="vm.list_packages", target_server_id="hub1", payload=payload)
        await _set_single_attempt(tid)
        await vms.vm_list_packages.original_func(tid)
        t = await fetch_task(tid)
        assert t.status == TaskStatus.FAILED
        assert t.last_error and "NO_PACKAGE_MANAGER" in t.last_error

    async def test_invalid_pattern_rejected_before_session(self, make_task, fetch_task, captured_audit, stub_session_and_callbacks, monkeypatch):
        calls = self._stub_packages(monkeypatch)
        fake = _FakeSshClient()
        stub_session_and_callbacks["holder"]["ssh"] = fake
        payload = {
            "vm_id": "vmp5", "hub_host": "10.0.0.7", "vm_name": "station-a",
            "guest_ip": "192.168.100.24", "pattern": "htop; rm -rf /", "is_managed": True,
        }
        tid = await make_task(task_kind="vm.list_packages", target_server_id="hub1", payload=payload)
        await _set_single_attempt(tid)
        await vms.vm_list_packages.original_func(tid)
        t = await fetch_task(tid)
        assert t.status == TaskStatus.FAILED
        assert t.last_error and "INVALID_PATTERN" in t.last_error
        assert calls == []

    async def test_empty_result_when_query_nonzero_no_stderr(self, make_task, fetch_task, captured_audit, stub_session_and_callbacks, monkeypatch):
        self._stub_packages(monkeypatch)
        fake = _FakeSshClient()
        # dpkg-query отдаёт rc=1 без stderr, когда ничего не подошло под pattern
        fake.set_response("dpkg-query -W", 1, "")
        stub_session_and_callbacks["holder"]["ssh"] = fake
        payload = {
            "vm_id": "vmp6", "hub_host": "10.0.0.7", "vm_name": "station-a",
            "guest_ip": "192.168.100.24", "os_family": "apt", "pattern": "nope*",
            "is_managed": True,
        }
        tid = await make_task(task_kind="vm.list_packages", target_server_id="hub1", payload=payload)
        await vms.vm_list_packages.original_func(tid)
        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        assert t.result["count"] == 0
        assert t.result["packages"] == []

