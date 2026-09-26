"""Тесты worker-таски `vm.prepare_for_test`.

* happy path: на hub'е destroy → snapshot-revert → start (домен после отката
  выключен), отчёт снимка/питания, вход в гостя учёткой из stash, тестовая
  учётка, тот же хвост `run_setup_pipeline` (PAM, ядро, режим, ребут);
  сессия в гостя — по IP гостя, ключевая (managed) или парольная;
* провал отката — `failed_step=vm_revert`, до гостя не доходим;
* гость не вернулся в сеть — `vm_revert`; не вошли/не готов — `prepare`;
* `skip_mode_switch` (`revert_only`) — ядро меняется, режим нет;
* `skip_pam_fix` — PAM-правки нет даже при флаге профиля;
* `vm.stand_setup` — без отката и учётки, тот же хвост без ядра и режима,
  вход в гостя учёткой из stash, исход — `submit_vm_stand_setup_result`.

SSH — `_FakeSshClient` (ordered script) из `test_prepare_for_test_task.py`,
Redis-stash — там же совместимый fake с настоящим envelope-шифрованием.
"""

from __future__ import annotations

import pytest

from src.clients.ssh import SshError
from src.core.constants import TaskStatus
from src.tasks import prepare_for_test as pft_task
from src.tasks import vms_prepare_for_test as vpt
from tests.test_prepare_for_test_task import (
    _KERNEL_STEPS,
    _PAM_STEP,
    CREDS_KEY,
    _FakeRedisClient,
    _FakeSshClient,
    _set_single_attempt,
    _test_creds_stash,
)


def _stash(**guest) -> dict:
    return _test_creds_stash(**(guest or {"guest_login_user": "dbos", "guest_private_key": "PEM-guest"}))


def _payload(**overrides) -> dict:
    payload = {
        "server_id": "srv_hub_1",
        "host": "10.0.0.1",
        "ssh_port": 22,
        "is_managed": True,
        "management_user": "dbos",
        "target_department_id": "dep_a",
        "vm_id": "vm_pft_1",
        "vm_name": "stand6",
        "snapshot_id": "vms_1",
        "snapshot_name": "1.7.5.9",
        "guest_ip": "10.0.0.66",
        "guest_ssh_port": 22,
        "prepare_request_id": "prep_vm_1",
        "kernel": "5.10.0",
        "mode": "orel",
        "test_creds_key": CREDS_KEY,
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def env(monkeypatch):
    """Подмена hub-сессии, сессии гостя, сети и callback'ов server_service."""
    state = {"submit": [], "reports": [], "guest_creds": [], "reachable": True}

    async def _await(*_args, **_kwargs) -> bool:
        return state["reachable"]

    async def _verify(*_args, **_kwargs) -> None:
        if state.get("verify_error"):
            raise state["verify_error"]

    monkeypatch.setattr(pft_task, "_await_reachability", _await)
    monkeypatch.setattr(pft_task, "verify_system_running", _verify)

    async def _submit(vm_id, request_id, succeeded, target_department_id=None, *, failed_step=None, error_message=None):
        state["submit"].append({
            "vm_id": vm_id, "request_id": request_id, "succeeded": succeeded,
            "failed_step": failed_step, "error_message": error_message,
        })
        return {"ok": True}

    async def _snapshots(vm_id, entries, target_department_id=None):
        state["reports"].append(("snapshots", entries))
        return {}

    async def _state(vm_id, target_department_id=None, **kwargs):
        state["reports"].append(("state", kwargs))
        return {}

    monkeypatch.setattr(vpt.server_service_client, "submit_vm_prepare_for_test_result", _submit)
    monkeypatch.setattr(vpt.server_service_client, "submit_vm_snapshots", _snapshots)
    monkeypatch.setattr(vpt.server_service_client, "submit_vm_state", _state)

    def install(*, hub_script, guest_script, stash):
        hub = _FakeSshClient(hub_script)
        guest = _FakeSshClient(guest_script)

        async def _open_hub(payload):
            return hub, payload["host"]

        def _build(creds, sid):
            state["guest_creds"].append(dict(creds))
            return guest

        monkeypatch.setattr(vpt, "open_hub_session", _open_hub)
        monkeypatch.setattr(pft_task.ssh_client, "build_session", _build)
        monkeypatch.setattr(pft_task.redis_pool, "get_redis", lambda: _FakeRedisClient(CREDS_KEY, stash))
        return hub, guest

    state["install"] = install
    return state


_HUB_REVERT_SHUT_OFF = [
    ("virsh destroy stand6", (1, "", "domain is not running")),
    ("virsh snapshot-revert --domain stand6 --snapshotname 1.7.5.9", (0, "", "")),
    ("virsh domstate stand6", (0, "shut off", "")),
    ("virsh start stand6", (0, "", "")),
    ("virsh domstate stand6", (0, "running", "")),
]
_GUEST_TAIL = [
    _PAM_STEP,
    *_KERNEL_STEPS,
    ("astra-modeswitch get", (0, "0", "")),
    ("reboot", (0, "", "")),
]


class TestHappyPath:
    async def test_revert_start_and_same_tail(self, env, make_task, fetch_task):
        hub, guest = env["install"](hub_script=_HUB_REVERT_SHUT_OFF, guest_script=_GUEST_TAIL, stash=_stash())
        tid = await make_task(task_kind="vm.prepare_for_test", target_server_id="srv_hub_1", payload=_payload())
        await vpt.vm_prepare_for_test.original_func(tid)

        task = await fetch_task(tid)
        assert task.status == TaskStatus.SUCCEEDED, task.last_error
        assert env["submit"] == [{
            "vm_id": "vm_pft_1", "request_id": "prep_vm_1", "succeeded": True,
            "failed_step": None, "error_message": None,
        }]
        # Отчёт отката — как у vm.snapshot_revert: текущий снимок + питание.
        assert env["reports"][0] == ("snapshots", [{"name": "1.7.5.9", "state": "ready", "is_current": True, "snapshot_id": "vms_1"}])
        assert env["reports"][1] == ("state", {"power_state": "on"})
        # Сессия в гостя — по IP гостя, ключом управляющего пользователя из stash.
        creds = env["guest_creds"][0]
        assert creds["host"] == "10.0.0.66"
        assert creds["is_managed"] is True
        assert creds["management_user"] == "dbos"
        assert creds["management_private_key"] == "PEM-guest"
        assert guest.create_user_calls[0]["login"] == "u"
        assert guest.create_user_calls[0]["force_replace"] is True

    async def test_running_after_revert_is_not_started_again(self, env, make_task, fetch_task):
        hub_script = [
            ("virsh destroy stand6", (0, "", "")),
            ("virsh snapshot-revert", (0, "", "")),
            ("virsh domstate stand6", (0, "running", "")),
        ]
        hub, _guest = env["install"](
            hub_script=hub_script, guest_script=_GUEST_TAIL,
            stash=_stash(guest_login_user="u", guest_password="1"),
        )
        tid = await make_task(task_kind="vm.prepare_for_test", target_server_id="srv_hub_1", payload=_payload())
        await vpt.vm_prepare_for_test.original_func(tid)

        task = await fetch_task(tid)
        assert task.status == TaskStatus.SUCCEEDED, task.last_error
        assert not any("virsh start" in c for c in hub.commands)
        # Не-managed ВМ — вход по паролю базовой учётки образа.
        creds = env["guest_creds"][0]
        assert (creds["is_managed"], creds["login"], creds["password"]) == (False, "u", "1")


class TestFailures:
    async def test_revert_failure_reports_vm_revert(self, env, make_task, fetch_task):
        hub_script = [
            ("virsh destroy stand6", (0, "", "")),
            ("virsh snapshot-revert", (1, "", "snapshot not found")),
        ]
        _hub, guest = env["install"](hub_script=hub_script, guest_script=[], stash=_stash())
        tid = await make_task(task_kind="vm.prepare_for_test", target_server_id="srv_hub_1", payload=_payload())
        await _set_single_attempt(tid)
        await vpt.vm_prepare_for_test.original_func(tid)

        task = await fetch_task(tid)
        assert task.status == TaskStatus.FAILED
        assert "VM_SNAPSHOT_FAILED" in (task.last_error or "")
        assert env["submit"][-1]["succeeded"] is False
        assert env["submit"][-1]["failed_step"] == "vm_revert"
        assert guest.commands == [] and guest.create_user_calls == []

    async def test_guest_not_back_reports_vm_revert(self, env, make_task, fetch_task):
        env["reachable"] = False
        env["install"](hub_script=_HUB_REVERT_SHUT_OFF, guest_script=[], stash=_stash())
        tid = await make_task(task_kind="vm.prepare_for_test", target_server_id="srv_hub_1", payload=_payload())
        await _set_single_attempt(tid)
        await vpt.vm_prepare_for_test.original_func(tid)

        assert (await fetch_task(tid)).status == TaskStatus.FAILED
        assert env["submit"][-1]["failed_step"] == "vm_revert"
        assert "VM_GUEST_UNREACHABLE" in env["submit"][-1]["error_message"]

    async def test_guest_not_ready_reports_prepare(self, env, make_task, fetch_task):
        env["verify_error"] = SshError(error_code="SSH_SYSTEM_NOT_READY", host="10.0.0.66", message="degraded")
        env["install"](hub_script=_HUB_REVERT_SHUT_OFF, guest_script=[], stash=_stash())
        tid = await make_task(task_kind="vm.prepare_for_test", target_server_id="srv_hub_1", payload=_payload())
        await _set_single_attempt(tid)
        await vpt.vm_prepare_for_test.original_func(tid)

        assert (await fetch_task(tid)).status == TaskStatus.FAILED
        assert env["submit"][-1]["failed_step"] == "prepare"

    async def test_kernel_failure_in_shared_tail(self, env, make_task, fetch_task):
        guest_script = [
            _PAM_STEP,
            ("dpkg -s linux-image", (0, "", "")),
            ("dpkg -s linux-headers", (0, "", "")),
            ("dpkg -s linux-astra-modules", (0, "", "")),
            ("grep menuentry_id", (0, "", "")),
        ]
        env["install"](hub_script=_HUB_REVERT_SHUT_OFF, guest_script=guest_script, stash=_stash())
        tid = await make_task(task_kind="vm.prepare_for_test", target_server_id="srv_hub_1", payload=_payload())
        await _set_single_attempt(tid)
        await vpt.vm_prepare_for_test.original_func(tid)

        assert (await fetch_task(tid)).status == TaskStatus.FAILED
        assert env["submit"][-1]["failed_step"] == "kernel_change"

    async def test_missing_guest_login_fails_before_hub(self, env, make_task, fetch_task):
        hub, _guest = env["install"](hub_script=[], guest_script=[], stash=_test_creds_stash())
        tid = await make_task(task_kind="vm.prepare_for_test", target_server_id="srv_hub_1", payload=_payload())
        await _set_single_attempt(tid)
        await vpt.vm_prepare_for_test.original_func(tid)

        assert (await fetch_task(tid)).status == TaskStatus.FAILED
        assert env["submit"][-1]["failed_step"] == "vm_revert"
        assert hub.commands == []


class TestRevertOnly:
    async def test_skip_mode_switch_keeps_kernel_change(self, env, make_task, fetch_task):
        guest_script = [_PAM_STEP, *_KERNEL_STEPS, ("reboot", (0, "", ""))]
        _hub, guest = env["install"](hub_script=_HUB_REVERT_SHUT_OFF, guest_script=guest_script, stash=_stash())
        tid = await make_task(
            task_kind="vm.prepare_for_test", target_server_id="srv_hub_1",
            payload=_payload(mode="smolensk", skip_mode_switch=True),
        )
        await vpt.vm_prepare_for_test.original_func(tid)

        assert (await fetch_task(tid)).status == TaskStatus.SUCCEEDED
        assert env["submit"][-1]["succeeded"] is True
        assert not any("astra-modeswitch" in c or "control enable" in c for c in guest.commands)
        assert any("GRUB_DEFAULT=" in c for c in guest.commands)
        assert guest.create_user_calls[0]["login"] == "u"


class TestSkipPamFix:
    async def test_flag_skips_pam_step(self, env, make_task, fetch_task):
        guest_script = [*_KERNEL_STEPS, ("astra-modeswitch get", (0, "0", "")), ("reboot", (0, "", ""))]
        _hub, guest = env["install"](hub_script=_HUB_REVERT_SHUT_OFF, guest_script=guest_script, stash=_stash())
        tid = await make_task(
            task_kind="vm.prepare_for_test", target_server_id="srv_hub_1",
            payload=_payload(provisioning={"disable_pam_lastlog_inactive": True}, skip_pam_fix=True),
        )
        await vpt.vm_prepare_for_test.original_func(tid)

        assert (await fetch_task(tid)).status == TaskStatus.SUCCEEDED
        assert env["submit"][-1]["succeeded"] is True
        assert not any("pam_lastlog" in c for c in guest.commands)
        assert any("GRUB_DEFAULT=" in c for c in guest.commands)

    async def test_default_keeps_pam_step(self, env, make_task, fetch_task):
        _hub, guest = env["install"](hub_script=_HUB_REVERT_SHUT_OFF, guest_script=_GUEST_TAIL, stash=_stash())
        tid = await make_task(
            task_kind="vm.prepare_for_test", target_server_id="srv_hub_1",
            payload=_payload(provisioning={"disable_pam_lastlog_inactive": True}),
        )
        await vpt.vm_prepare_for_test.original_func(tid)

        assert (await fetch_task(tid)).status == TaskStatus.SUCCEEDED
        assert any("pam_lastlog" in c for c in guest.commands)


def _setup_payload(**overrides) -> dict:
    payload = {
        "vm_id": "vm_ss_1",
        "stand_setup_request_id": "ssr_vm_1",
        "target_department_id": "dep_a",
        "guest_ip": "10.0.0.66",
        "guest_ssh_port": 22,
        "test_username": "u",
        "stand_setup": {"kernel_cmdline_extra": ["maxcpus=8"], "reboot_after": True},
        "provisioning": {"disable_pam_lastlog_inactive": True},
        "stash_key": CREDS_KEY,
    }
    payload.update(overrides)
    return payload


class TestVmStandSetup:
    @pytest.fixture
    def setup_submit(self, monkeypatch):
        calls: list[dict] = []

        async def _submit(vm_id, request_id, succeeded, target_department_id=None, *,
                          failed_step=None, error_message=None):
            calls.append({"vm_id": vm_id, "request_id": request_id, "succeeded": succeeded,
                          "target_department_id": target_department_id, "failed_step": failed_step})
            return {"ok": True}

        monkeypatch.setattr(vpt.server_service_client, "submit_vm_stand_setup_result", _submit)
        return calls

    async def test_runs_steps_on_guest_and_calls_back(self, env, make_task, fetch_task, setup_submit):
        guest_script = [
            _PAM_STEP,
            ("GRUB_CMDLINE_LINUX_DEFAULT", (0, 'GRUB_CMDLINE_LINUX_DEFAULT="quiet"\n', "")),
            ("sed -i 's|^GRUB_CMDLINE_LINUX_DEFAULT", (0, "", "")),
            ("update-grub", (0, "", "")),
            ("reboot", (0, "", "")),
        ]
        hub, guest = env["install"](
            hub_script=[], guest_script=guest_script,
            stash={"guest_login_user": "snapuser", "guest_password": "snap-pass"},
        )
        tid = await make_task(task_kind="vm.stand_setup", target_server_id="srv_hub_1", payload=_setup_payload())
        await vpt.vm_stand_setup.original_func(tid)

        task = await fetch_task(tid)
        assert task.status == TaskStatus.SUCCEEDED, task.last_error
        assert setup_submit == [{
            "vm_id": "vm_ss_1", "request_id": "ssr_vm_1", "succeeded": True,
            "target_department_id": "dep_a", "failed_step": None,
        }]
        # Без отката, учётки, ядра и режима; сессия — в гостя, учёткой из stash.
        assert hub.commands == []
        assert guest.create_user_calls == []
        assert not any("astra-modeswitch" in c or "dpkg -s" in c for c in guest.commands)
        creds = env["guest_creds"][0]
        assert (creds["host"], creds["is_managed"], creds["login"], creds["password"]) == (
            "10.0.0.66", False, "snapuser", "snap-pass",
        )

    async def test_script_from_stash_and_failure_reports_step(self, env, make_task, fetch_task, setup_submit):
        guest_script = [
            _PAM_STEP,
            ("bash -s", (3, "boom\n", "")),
        ]
        env["install"](
            hub_script=[], guest_script=guest_script,
            stash={"guest_login_user": "dbos", "guest_private_key": "PEM-guest", "stand_setup_script": "false\n"},
        )
        payload = _setup_payload(stand_setup={"phase": "after_boot", "reboot_after": False})
        tid = await make_task(task_kind="vm.stand_setup", target_server_id="srv_hub_1", payload=payload)
        await _set_single_attempt(tid)
        await vpt.vm_stand_setup.original_func(tid)

        assert (await fetch_task(tid)).status == TaskStatus.FAILED
        assert setup_submit[-1]["succeeded"] is False
        assert setup_submit[-1]["failed_step"] == "stand_setup"
