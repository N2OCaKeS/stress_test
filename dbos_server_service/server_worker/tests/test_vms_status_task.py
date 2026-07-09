"""Тесты worker-таски `vm.status` — периодическая проба статуса ВМ.

Зеркало серверного `power.status`, но для ВМ: три сигнала снимаются через
управляющую SSH-сессию к hub'у — питание (`virsh domstate`) + ping гостя + TCP
SSH-порт гостя. SSH мокается `_FakeSshClient` (дефолт rc=0, точечные ответы по
подстроке), `open_hub_session` и `submit_vm_state` — monkeypatch'ем.
"""

from __future__ import annotations

import pytest

from src.core.constants import TaskStatus
from src.tasks import vms_status


class _FakeSshClient:
    """Мок SshClient: отдаёт заданный ответ по подстроке команды (деф. (0,'',''))."""

    def __init__(self, host: str = "10.0.0.7"):
        self.host = host
        self._responses: list[tuple[str, tuple[int, str, str]]] = []
        self.commands: list[str] = []

    def set_response(self, pat: str, rc: int, stdout: str = "", stderr: str = ""):
        self._responses.append((pat, (rc, stdout, stderr)))

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return None

    async def close(self):
        return None

    async def run(self, command, *, sudo=False, stdin_payload=None):
        self.commands.append(command)
        for pat, resp in self._responses:
            if pat in command:
                return resp
        return (0, "", "")

    def ran(self, pat: str) -> bool:
        return any(pat in c for c in self.commands)


@pytest.fixture(autouse=True)
def stub_session_and_callback(monkeypatch):
    """Замокать open_hub_session (отдаёт fake) и submit_vm_state (ловит вызовы)."""
    holder: dict = {"ssh": None}
    calls: list[dict] = []

    async def _open(payload):  # noqa: ARG001
        fake = holder["ssh"]
        return fake, fake.host

    async def _vm_state(vm_id, target_department_id=None, **kw):
        calls.append({"vm_id": vm_id, "target_department_id": target_department_id, **kw})
        return {"ok": True}

    monkeypatch.setattr(vms_status, "open_hub_session", _open)
    monkeypatch.setattr(
        vms_status.server_service_client, "submit_vm_state", _vm_state,
    )
    return {"holder": holder, "calls": calls}


def _payload(**over) -> dict:
    base = {
        "vm_id": "vm1", "hub_host": "10.0.0.7", "name": "station-a",
        "vm_name": "station-a", "target_department_id": "dep1",
        "is_managed": True,
    }
    base.update(over)
    return base


class TestVmStatus:
    async def test_probes_power_ping_ssh(
        self, make_task, fetch_task, captured_audit, stub_session_and_callback,
    ):
        fake = _FakeSshClient()
        fake.set_response("virsh domstate", 0, "running")
        stub_session_and_callback["holder"]["ssh"] = fake

        tid = await make_task(
            task_kind="vm.status", target_server_id="hub1",
            payload=_payload(guest_ip="10.177.103.101"),
        )
        await vms_status.vm_status.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        # bridge-ВМ с известным LAN-адресом: ping + ssh пробуются на hub'е.
        assert fake.ran("ping -c 1")
        assert fake.ran("/dev/tcp/10.177.103.101/22")
        # Callback несёт все три сигнала.
        call = stub_session_and_callback["calls"][-1]
        assert call["power_state"] == "on"
        assert call["ping_reachable"] is True
        assert call["ssh_reachable"] is True
        assert t.result["power_state"] == "on"
        assert t.result["ping_reachable"] is True
        assert t.result["ssh_reachable"] is True

    async def test_domstate_off_maps_off(
        self, make_task, fetch_task, captured_audit, stub_session_and_callback,
    ):
        fake = _FakeSshClient()
        fake.set_response("virsh domstate", 0, "shut off")
        stub_session_and_callback["holder"]["ssh"] = fake

        tid = await make_task(
            task_kind="vm.status", target_server_id="hub1",
            payload=_payload(guest_ip="10.177.103.101"),
        )
        await vms_status.vm_status.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        assert stub_session_and_callback["calls"][-1]["power_state"] == "off"

    async def test_slirp_no_ip_reachable_false_not_error(
        self, make_task, fetch_task, captured_audit, stub_session_and_callback,
    ):
        # NAT/SLIRP: адреса в payload нет, а domifaddr (lease/agent) пуст —
        # гость из LAN недостижим. ping/ssh → False, но это НЕ ошибка: питание
        # снимается, таска успешна.
        fake = _FakeSshClient()
        fake.set_response("virsh domstate", 0, "running")
        fake.set_response("virsh domifaddr", 0, "")  # ни lease, ни agent-адреса
        stub_session_and_callback["holder"]["ssh"] = fake

        tid = await make_task(
            task_kind="vm.status", target_server_id="hub1",
            payload=_payload(),  # без guest_ip
        )
        await vms_status.vm_status.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        assert not fake.ran("ping -c 1")  # без адреса пробы не пускаем
        call = stub_session_and_callback["calls"][-1]
        assert call["power_state"] == "on"
        assert call["ping_reachable"] is False
        assert call["ssh_reachable"] is False
