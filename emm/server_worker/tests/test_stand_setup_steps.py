"""`pam_fix`, `stand_setup`, параметры ядра, degraded-allowlist.

SSH — `_FakeSshClient` из `test_prepare_for_test_task` (ordered script).
"""

from __future__ import annotations

import pytest

from src.core.constants import TaskStatus
from src.tasks import _stand_setup_helpers as helpers
from src.tasks import acs_snapshots
from src.tasks import prepare_for_test as pft_task
from tests.test_prepare_for_test_task import (
    CREDS_KEY,
    _FakeRedisClient,
    _FakeSshClient,
    _KERNEL_STEPS,
    _payload,
    _patch_redis,
    _patch_ssh,
    _set_single_attempt,
    _test_creds_stash,
    captured_submit,  # noqa: F401 — фикстура
    stub_collaborators,  # noqa: F401 — autouse-фикстура модуля-источника
)


class _RecordingSsh(_FakeSshClient):
    """Как `_FakeSshClient`, но запоминает stdin_payload."""

    def __init__(self, script):
        super().__init__(script)
        self.stdin: list[str | None] = []

    async def run(self, command: str, *, sudo: bool = False, stdin_payload=None):
        self.stdin.append(stdin_payload)
        return await super().run(command, sudo=sudo, stdin_payload=stdin_payload)


class TestKernelCmdline:
    def test_merge_is_idempotent(self):
        assert helpers.merge_cmdline("quiet splash", ["audit=0"]) == "quiet splash audit=0"
        assert helpers.merge_cmdline("quiet audit=0", ["audit=0"]) is None

    async def test_adds_param_once(self):
        ssh = _FakeSshClient([
            ("GRUB_CMDLINE_LINUX_DEFAULT", (0, 'GRUB_CMDLINE_LINUX_DEFAULT="quiet"\n', "")),
            ("sed -i", (0, "", "")),
        ])
        assert await helpers.add_kernel_cmdline_params(ssh, ["audit=0"], "h") is True
        assert ssh.commands[1] == (
            "sed -i 's|^GRUB_CMDLINE_LINUX_DEFAULT=.*|GRUB_CMDLINE_LINUX_DEFAULT=\"quiet audit=0\"|' /etc/default/grub"
        )

        again = _FakeSshClient([
            ("GRUB_CMDLINE_LINUX_DEFAULT", (0, 'GRUB_CMDLINE_LINUX_DEFAULT="quiet audit=0"\n', "")),
        ])
        assert await helpers.add_kernel_cmdline_params(again, ["audit=0"], "h") is False
        assert len(again.commands) == 1  # повторный прогон ничего не пишет

    async def test_rejects_unsafe_param(self):
        from src.clients.ssh import SshError

        with pytest.raises(SshError) as exc:
            await helpers.add_kernel_cmdline_params(_FakeSshClient([]), ["audit=0|rm -rf"], "h")
        assert exc.value.error_code == "PREPARE_FOR_TEST_INVALID_CMDLINE"


class TestPipelineWithStandSetup:
    async def test_audit_off_params_script_and_second_reboot(
        self, monkeypatch, make_task, fetch_task, captured_submit,
    ):
        reboots: list[str] = []
        fake = _RecordingSsh([
            ("pam_lastlog", (0, "", "")),
            ("GRUB_CMDLINE_LINUX_DEFAULT", (0, 'GRUB_CMDLINE_LINUX_DEFAULT="quiet"\n', "")),
            ("sed -i 's|^GRUB_CMDLINE_LINUX_DEFAULT", (0, "", "")),
            *_KERNEL_STEPS,
            ("astra-modeswitch get", (0, "0", "")),
            ("reboot", (0, "", "")),
            ("bash -s", (0, "tuned ok\n", "")),
            ("reboot", (0, "", "")),
        ])
        _patch_ssh(monkeypatch, fake)
        _patch_redis(monkeypatch, _FakeRedisClient(
            CREDS_KEY, _test_creds_stash(stand_setup_script="echo tune {{secret}}\n"),
        ))

        async def _verify(host, factory, *, label, provisioning=None, reboot=None):
            reboots.append(label)
            assert provisioning["allowed_failed_units"] == ["astra-mount-lock.service"]
            assert reboot is not None

        monkeypatch.setattr(pft_task, "verify_system_running", _verify)
        payload = _payload(mode="orel")
        payload["stand_setup"] = {
            "kernel_cmdline_extra": ["audit=0"], "run_as": "root", "phase": "after_boot",
            "reboot_after": True, "timeout_seconds": 60,
        }
        payload["provisioning"] = {
            "allowed_failed_units": ["astra-mount-lock.service"], "degraded_reboot_attempts": 3,
            "disable_pam_lastlog_inactive": True,
        }
        tid = await make_task(task_kind="server.prepare_for_test", target_server_id="srv_ss_1", payload=payload)
        await pft_task.server_prepare_for_test.original_func(tid)

        assert (await fetch_task(tid)).status == TaskStatus.SUCCEEDED
        assert captured_submit[0]["succeeded"] is True
        # Скрипт — из stash (текст в payload не едет), через stdin `bash -s`.
        idx = next(i for i, c in enumerate(fake.commands) if c == "bash -s")
        assert fake.stdin[idx] == "echo tune {{secret}}\n"
        # Две перезагрузки: после ядра/режима и после скрипта (reboot_after).
        assert sum(c == "reboot" for c in fake.commands) == 2
        assert len(reboots) == 2
        # Одна правка grub и один update-grub на ядро + параметры.
        assert sum(c == "update-grub" for c in fake.commands) == 1

    async def test_script_failure_reports_stand_setup_with_tail(
        self, monkeypatch, make_task, fetch_task, captured_submit,
    ):
        fake = _FakeSshClient([
            ("pam_lastlog", (0, "", "")),
            *_KERNEL_STEPS,
            ("astra-modeswitch get", (0, "0", "")),
            ("reboot", (0, "", "")),
            ("sudo -u u -H bash -s", (3, "step 1 ok\nboom: tuning failed\n", "")),
        ])
        _patch_ssh(monkeypatch, fake)
        _patch_redis(monkeypatch, _FakeRedisClient(CREDS_KEY, _test_creds_stash(stand_setup_script="false\n")))
        payload = _payload(mode="orel", server_id="srv_ss_2")
        payload["stand_setup"] = {"run_as": "test_user", "phase": "after_boot", "reboot_after": True}
        tid = await make_task(task_kind="server.prepare_for_test", target_server_id="srv_ss_2", payload=payload)
        await _set_single_attempt(tid)
        await pft_task.server_prepare_for_test.original_func(tid)

        assert (await fetch_task(tid)).status == TaskStatus.FAILED
        assert captured_submit[0]["failed_step"] == "stand_setup"
        assert "boom: tuning failed" in captured_submit[0]["error_message"]

    async def test_pam_fix_skipped_when_flag_off(
        self, monkeypatch, make_task, fetch_task, captured_submit,
    ):
        fake = _FakeSshClient([
            *_KERNEL_STEPS,
            ("astra-modeswitch get", (0, "0", "")),
            ("reboot", (0, "", "")),
        ])
        _patch_ssh(monkeypatch, fake)
        _patch_redis(monkeypatch, _FakeRedisClient(CREDS_KEY, _test_creds_stash()))
        payload = _payload(mode="orel", server_id="srv_ss_3")
        payload["provisioning"] = {"disable_pam_lastlog_inactive": False}
        tid = await make_task(task_kind="server.prepare_for_test", target_server_id="srv_ss_3", payload=payload)
        await pft_task.server_prepare_for_test.original_func(tid)

        assert (await fetch_task(tid)).status == TaskStatus.SUCCEEDED
        assert not any("pam_lastlog" in c for c in fake.commands)


class TestSkipPamFix:
    def test_pipeline_provisioning(self):
        on = {"provisioning": {"disable_pam_lastlog_inactive": True}}
        assert pft_task.pipeline_provisioning(on)["disable_pam_lastlog_inactive"] is True
        assert pft_task.pipeline_provisioning({**on, "skip_pam_fix": False})["disable_pam_lastlog_inactive"] is True
        assert pft_task.pipeline_provisioning({**on, "skip_pam_fix": True})["disable_pam_lastlog_inactive"] is False
        # Без профиля PAM-правка включена легаси-умолчанием — флаг снимает и её.
        assert pft_task.pipeline_provisioning({"skip_pam_fix": True})["disable_pam_lastlog_inactive"] is False
        assert helpers.DEFAULT_PROVISIONING["disable_pam_lastlog_inactive"] is True

    async def test_flag_overrides_profile(
        self, monkeypatch, make_task, fetch_task, captured_submit,
    ):
        fake = _FakeSshClient([
            *_KERNEL_STEPS,
            ("astra-modeswitch get", (0, "0", "")),
            ("reboot", (0, "", "")),
        ])
        _patch_ssh(monkeypatch, fake)
        _patch_redis(monkeypatch, _FakeRedisClient(CREDS_KEY, _test_creds_stash()))
        payload = _payload(mode="orel", server_id="srv_ss_nopam")
        payload["provisioning"] = {"disable_pam_lastlog_inactive": True}
        payload["skip_pam_fix"] = True
        tid = await make_task(task_kind="server.prepare_for_test", target_server_id="srv_ss_nopam", payload=payload)
        await pft_task.server_prepare_for_test.original_func(tid)

        assert (await fetch_task(tid)).status == TaskStatus.SUCCEEDED
        assert captured_submit[0]["succeeded"] is True
        assert not any("pam_lastlog" in c for c in fake.commands)
        assert any("GRUB_DEFAULT=" in c for c in fake.commands)

    async def test_default_runs_pam_fix(
        self, monkeypatch, make_task, fetch_task, captured_submit,
    ):
        fake = _FakeSshClient([
            ("pam_lastlog", (0, "", "")),
            *_KERNEL_STEPS,
            ("astra-modeswitch get", (0, "0", "")),
            ("reboot", (0, "", "")),
        ])
        _patch_ssh(monkeypatch, fake)
        _patch_redis(monkeypatch, _FakeRedisClient(CREDS_KEY, _test_creds_stash()))
        payload = _payload(mode="orel", server_id="srv_ss_pam")
        payload["provisioning"] = {"disable_pam_lastlog_inactive": True}
        tid = await make_task(task_kind="server.prepare_for_test", target_server_id="srv_ss_pam", payload=payload)
        await pft_task.server_prepare_for_test.original_func(tid)

        assert (await fetch_task(tid)).status == TaskStatus.SUCCEEDED
        assert any("pam_lastlog" in c for c in fake.commands)


class TestRevertOnlySkipsModeSwitch:
    async def test_kernel_changed_mode_untouched(
        self, monkeypatch, make_task, fetch_task, captured_submit,
    ):
        fake = _FakeSshClient([
            ("pam_lastlog", (0, "", "")),
            *_KERNEL_STEPS,
            ("reboot", (0, "", "")),
        ])
        _patch_ssh(monkeypatch, fake)
        _patch_redis(monkeypatch, _FakeRedisClient(CREDS_KEY, _test_creds_stash()))
        payload = _payload(mode="smolensk", server_id="srv_ss_ro")
        payload["skip_mode_switch"] = True
        tid = await make_task(task_kind="server.prepare_for_test", target_server_id="srv_ss_ro", payload=payload)
        await pft_task.server_prepare_for_test.original_func(tid)

        assert (await fetch_task(tid)).status == TaskStatus.SUCCEEDED
        assert captured_submit[0]["succeeded"] is True
        assert captured_submit[0]["error_message"] is None
        assert not any("astra-modeswitch" in c or "control enable" in c for c in fake.commands)
        # Ядро переключено и стенд перезагружен.
        assert any("GRUB_DEFAULT=" in c for c in fake.commands)
        assert fake.commands[-1] == "reboot"


class TestStandSetupWithoutRestore:
    async def test_runs_steps_and_calls_back(self, monkeypatch, make_task, fetch_task):
        calls: list[dict] = []

        async def _submit(server_id, request_id, succeeded, target_department_id=None, *,
                          failed_step=None, error_message=None):
            calls.append({"request_id": request_id, "succeeded": succeeded, "failed_step": failed_step})
            return {"ok": True}

        monkeypatch.setattr(pft_task.server_service_client, "submit_stand_setup_result", _submit)
        fake = _FakeSshClient([
            ("pam_lastlog", (0, "", "")),
            ("GRUB_CMDLINE_LINUX_DEFAULT", (0, 'GRUB_CMDLINE_LINUX_DEFAULT="quiet"\n', "")),
            ("sed -i 's|^GRUB_CMDLINE_LINUX_DEFAULT", (0, "", "")),
            ("update-grub", (0, "", "")),
            ("reboot", (0, "", "")),
        ])
        _patch_ssh(monkeypatch, fake)
        payload = {
            "server_id": "srv_ss_4", "stand_setup_request_id": "ssr_1", "target_department_id": "dep_a",
            "host": "10.0.0.9", "ssh_port": 22, "is_managed": True, "management_user": "dbos",
            "test_username": "u", "stand_setup": {"kernel_cmdline_extra": ["maxcpus=8"], "reboot_after": True},
            "provisioning": {"disable_pam_lastlog_inactive": True},
        }
        tid = await make_task(task_kind="server.stand_setup", target_server_id="srv_ss_4", payload=payload)
        await pft_task.server_stand_setup.original_func(tid)

        assert (await fetch_task(tid)).status == TaskStatus.SUCCEEDED
        assert calls == [{"request_id": "ssr_1", "succeeded": True, "failed_step": None}]
        # Ни ядра, ни режима, ни учётки.
        assert not any("astra-modeswitch" in c or "dpkg -s" in c for c in fake.commands)


class _StatusSession:
    """Сессия для `verify_system_running`: отвечает из очереди статусов."""

    def __init__(self, answers: list[tuple[str, str]]):
        self._answers = answers

    def __call__(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return None

    async def run(self, command: str, *, sudo: bool = False, stdin_payload=None):
        status, failed = self._answers[0]
        if "is-system-running" in command:
            return 0, status + "\n", ""
        self._answers.pop(0)
        return 0, failed, ""


class TestDegradedAllowlist:
    @pytest.fixture(autouse=True)
    def fast(self, monkeypatch):
        async def _no_sleep(_s):
            return None

        monkeypatch.setattr(acs_snapshots.asyncio, "sleep", _no_sleep)

    async def test_only_mount_lock_failed_is_ready(self):
        session = _StatusSession([("degraded", "astra-mount-lock.service\n")])
        await acs_snapshots.verify_system_running(
            "h", session, label="t", provisioning=helpers.parse_provisioning(None),
        )

    async def test_other_unit_reboots_up_to_limit_then_fails(self):
        from src.clients.ssh import SshError

        reboots: list[int] = []

        async def _reboot():
            reboots.append(1)

        session = _StatusSession([("degraded", "foo.service\n")] * 10)
        with pytest.raises(SshError) as exc:
            await acs_snapshots.verify_system_running(
                "h", session, label="t",
                provisioning={**helpers.parse_provisioning(None), "degraded_reboot_attempts": 2,
                              "boot_wait_timeout_seconds": 30},
                reboot=_reboot,
            )
        assert len(reboots) == 2
        assert "foo.service" in str(exc.value)

    async def test_reboot_fixes_degraded(self):
        session = _StatusSession([("degraded", "foo.service\n"), ("running", "")])
        reboots: list[int] = []

        async def _reboot():
            reboots.append(1)

        await acs_snapshots.verify_system_running(
            "h", session, label="t", provisioning=helpers.parse_provisioning(None), reboot=_reboot,
        )
        assert reboots == [1]

    async def test_without_provisioning_degraded_is_not_ready(self, monkeypatch):
        from src.clients.ssh import SshError

        monkeypatch.setattr(acs_snapshots.get_settings(), "acs_bootstrap_verify_retries", 2)
        session = _StatusSession([("degraded", "astra-mount-lock.service\n")] * 5)
        with pytest.raises(SshError):
            await acs_snapshots.verify_system_running("h", session, label="t")
