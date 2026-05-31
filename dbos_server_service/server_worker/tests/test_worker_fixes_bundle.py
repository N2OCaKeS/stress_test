"""Точечные unit-тесты на четыре фикса в server_worker.

Покрывает:

1. `ipmi_rotate_password.rotated_at` фиксируется ПОСЛЕ apply на BMC.
2. `_runner` cancel-fast-path подкладывает `task.cancelled_at` в
   timestamp audit-event'а (если поле выставлено).
3. `_runner.run_task` пишет `task.deleted_midrun` audit, когда row
   пропала между mark_running и terminal write, и НЕ шедулит retry.
4. `_install_authorized_key` отказывается работать, если `getent
   passwd` вернул пустой `home` (`mkdir -p /.ssh` под sudo — порча ФС).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import delete, update

from src.clients.ssh import SshClient, SshError
from src.core.constants import TaskStatus
from src.db.session import AsyncSessionLocal
from src.models import Task
from src.tasks import passwords
from src.tasks._runner import run_task
from tests._ssh_mock_helpers import make_conn, run_result


# ── helpers ─────────────────────────────────────────────────────────────────


class _FakeBmcCaptureOrder:
    """Stand-in для RedfishClient: фиксирует порядок rotate vs verify."""

    def __init__(self, *, raise_on_rotate: bool = False):
        self._raise_rotate = raise_on_rotate
        self.rotate_calls: list[tuple[int, str]] = []
        self.get_power_state_calls: int = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        pass

    async def rotate_user_password(self, user_id: int, new_password: str):
        if self._raise_rotate:
            from src.clients.redfish import RedfishError
            raise RedfishError(500, "patch failed")
        self.rotate_calls.append((user_id, new_password))

    async def get_power_state(self) -> str:
        self.get_power_state_calls += 1
        return "On"

    async def aclose(self) -> None:
        pass


def _patch_no_retry(monkeypatch) -> None:
    from src.tasks import _runner

    async def noop(*args, **kwargs):
        pass

    monkeypatch.setattr(_runner, "_schedule_retry", noop)


# ── 1. rotated_at ПОСЛЕ apply ───────────────────────────────────────────────


class TestIpmiRotatedAtAfterApply:
    """`rotated_at` должен сниматься после успешного BMC apply, не до."""

    async def test_rotated_at_taken_after_bmc_apply_happy(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        """Happy-path: apply делается раньше, чем берётся timestamp.

        Проверяем, что `rotated_at`, переданный в submit, больше или
        равен timestamp'у момента, в который BMC apply действительно
        завершился. До фикса rotated_at снимался до apply и был меньше.
        """
        tid = await make_task(
            task_kind="ipmi.rotate_password",
            target_server_id="srv_rot",
            payload={"server_id": "srv_rot"},
        )

        async def fake_fetch(server_id, target_department_id=None):
            return {
                "controller_id": "ipm_rot",
                "kind": "idrac",
                "endpoint_url": "https://bmc.test",
                "username": "root",
                "password": "old",
            }

        bmc = _FakeBmcCaptureOrder()
        timestamps: dict[str, str] = {}

        original_rotate = bmc.rotate_user_password

        async def wrap_rotate(user_id, new_password):
            await original_rotate(user_id, new_password)
            timestamps["apply_done"] = datetime.now(timezone.utc).isoformat()

        bmc.rotate_user_password = wrap_rotate  # type: ignore[assignment]

        submit_calls: list[dict] = []

        async def fake_submit(
            controller_id, new_password, rotated_at,
            target_department_id=None, verified_at=None,
        ):
            submit_calls.append({"rotated_at": rotated_at})
            return {"rotated_at": rotated_at}

        async def _bmc_factory(creds, *, prefer="redfish"):
            return bmc

        monkeypatch.setattr(
            "src.tasks.passwords.server_service_client.fetch_ipmi_credentials",
            fake_fetch,
        )
        monkeypatch.setattr(
            "src.tasks.passwords.server_service_client.submit_rotated_ipmi_password",
            fake_submit,
        )
        monkeypatch.setattr("src.tasks.passwords._get_bmc", _bmc_factory)

        await passwords.ipmi_rotate_password.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        assert submit_calls, "submit must be called on happy-path"
        rotated_at = submit_calls[0]["rotated_at"]
        apply_done = timestamps["apply_done"]
        # rotated_at >= apply_done (rotated_at снят ПОСЛЕ wrap_rotate
        # выставил apply_done). Сравниваем как ISO-строки — оба UTC, оба
        # с микросекундной точностью; лексикографическое сравнение
        # совпадает с временным.
        assert rotated_at >= apply_done, (
            f"rotated_at={rotated_at} must be >= apply_done={apply_done}"
        )

    async def test_rotated_at_not_recorded_when_apply_fails(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        """Apply упал → submit НЕ зовётся → rotated_at нигде не
        зафиксирован. До фикса rotated_at вычислялся ДО apply и
        утекал в локальную переменную, даже если задача падала."""
        tid = await make_task(
            task_kind="ipmi.rotate_password",
            target_server_id="srv_rot_fail",
            payload={"server_id": "srv_rot_fail"},
        )
        async with AsyncSessionLocal() as session:
            await session.execute(
                update(Task).where(Task.id == tid).values(max_attempts=1)
            )
            await session.commit()
        _patch_no_retry(monkeypatch)

        async def fake_fetch(server_id, target_department_id=None):
            return {
                "controller_id": "ipm_rot_fail",
                "kind": "idrac",
                "endpoint_url": "https://bmc.test",
                "username": "root",
                "password": "old",
            }

        submit_calls: list = []

        async def fake_submit(*args, **kwargs):
            submit_calls.append(args)
            return {"rotated_at": "ignored"}

        async def _bmc_factory(creds, *, prefer="redfish"):
            return _FakeBmcCaptureOrder(raise_on_rotate=True)

        monkeypatch.setattr(
            "src.tasks.passwords.server_service_client.fetch_ipmi_credentials",
            fake_fetch,
        )
        monkeypatch.setattr(
            "src.tasks.passwords.server_service_client.submit_rotated_ipmi_password",
            fake_submit,
        )
        monkeypatch.setattr("src.tasks.passwords._get_bmc", _bmc_factory)

        await passwords.ipmi_rotate_password.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.FAILED
        assert submit_calls == [], "submit must not be called when apply fails"


# ── 2. cancel-fast-path timestamp ───────────────────────────────────────────


class TestCancelFastPathTimestamp:
    """В cancel-fast-path audit-event должен нести `task.cancelled_at`,
    а не worker's now()."""

    async def test_cancel_fast_path_uses_task_cancelled_at(
        self, make_task, captured_audit,
    ):
        tid = await make_task(task_kind="power.on", target_server_id="srv_cz")

        # Имитируем server_service cancel: статус CANCELLED и явный
        # cancelled_at в прошлом — операторский момент, не текущий now().
        cancel_ts = datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)
        async with AsyncSessionLocal() as session:
            await session.execute(
                update(Task)
                .where(Task.id == tid)
                .values(
                    status=TaskStatus.CANCELLED,
                    cancelled_at=cancel_ts,
                    cancelled_by="usr_op",
                    cancel_reason="op-mistake",
                )
            )
            await session.commit()

        async def impl(_payload: dict) -> dict:
            pytest.fail("impl must not be called on cancel-fast-path")

        await run_task(
            tid,
            audit_action="server.power_on",
            audit_target_type="server",
            impl=impl,
        )

        assert len(captured_audit) == 1
        ev = captured_audit[0]
        assert ev["status"] == "failure"
        assert ev["details"]["reason"] == "task_cancelled"
        assert ev["details"]["cancelled_by"] == "usr_op"
        # Главная проверка: timestamp совпадает с cancelled_at, не с now().
        assert ev["timestamp"] == cancel_ts.isoformat()


# ── 3. deleted_midrun ──────────────────────────────────────────────────────


class TestDeletedMidrun:
    """Если row удалена между mark_running и terminal write — пишем
    `task.deleted_midrun` audit, retry не шедулим."""

    async def test_success_path_row_deleted_emits_deleted_midrun(
        self, make_task, fetch_task, captured_audit,
    ):
        tid = await make_task(task_kind="power.on", target_server_id="srv_d1")

        async def impl(_payload: dict) -> dict:
            # Имитируем retention cleanup / ручной DELETE row пока impl
            # успешно отрабатывает.
            async with AsyncSessionLocal() as session:
                await session.execute(delete(Task).where(Task.id == tid))
                await session.commit()
            return {"power_state": "on"}

        await run_task(
            tid,
            audit_action="server.power_on",
            audit_target_type="server",
            impl=impl,
            audit_safe_fields={"power_state"},
        )

        # Row действительно удалён.
        row = await fetch_task(tid)
        assert row is None

        # Audit: один event про deleted_midrun, не success.
        assert len(captured_audit) == 1
        ev = captured_audit[0]
        assert ev["action"] == "task.deleted_midrun"
        assert ev["status"] == "failure"
        assert ev["severity"] == "WARNING"
        assert ev["details"]["reason"] == "task_deleted_midrun"
        assert ev["details"]["original_action"] == "server.power_on"
        assert ev["details"]["phase"] == "success"

    async def test_failure_path_row_deleted_suppresses_retry(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        tid = await make_task(task_kind="power.on", target_server_id="srv_d2")
        # max_attempts=3 (default) — попадаем в retry-ветку, но deleted
        # должен её подавить.

        async def impl(_payload: dict) -> dict:
            async with AsyncSessionLocal() as session:
                await session.execute(delete(Task).where(Task.id == tid))
                await session.commit()
            raise RuntimeError("kaboom")

        retry_calls: list[tuple] = []
        from src.tasks import _runner as runner_mod

        async def fake_schedule_retry(*args, **kwargs):
            retry_calls.append((args, kwargs))

        monkeypatch.setattr(runner_mod, "_schedule_retry", fake_schedule_retry)

        await run_task(
            tid,
            audit_action="server.power_on",
            audit_target_type="server",
            impl=impl,
        )

        row = await fetch_task(tid)
        assert row is None
        # retry НЕ шедулится для deleted_midrun task'и.
        assert retry_calls == []
        assert len(captured_audit) == 1
        ev = captured_audit[0]
        assert ev["action"] == "task.deleted_midrun"
        assert ev["severity"] == "WARNING"
        assert ev["details"]["reason"] == "task_deleted_midrun"
        assert ev["details"]["original_action"] == "server.power_on"
        # impl-error всё ещё прокидываем — оператору пригодится знать,
        # на каком исключении task осталась без trace'а в DB.
        assert "kaboom" in ev["details"]["error"]


# ── 4. _install_authorized_key guard ────────────────────────────────────────


class TestInstallAuthorizedKeyHomeGuard:
    """`getent passwd` пустой home → отказ, не `mkdir -p /.ssh`."""

    async def test_bash_command_contains_empty_home_guard(self):
        ssh = SshClient(host="10.0.0.1", username="dbos", password="pwd")
        ssh._conn = make_conn([run_result("", "user x not found", 1)])

        with pytest.raises(SshError) as exc:
            await ssh._install_authorized_key(
                target_user="dbos",
                public_key=(
                    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakeKey "
                    "comment@host"
                ),
                truncate=False,
                error_code="SSH_AUTHORIZED_KEYS_FAILED",
            )

        # Команда содержит явный guard: если home пустой / "/" / системный
        # псевдо-аккаунт (`/dev`, `/var/empty` и т.п.) — exit 1.
        # Guard переехал с `[ -z ... ] || [ ... = / ]` на `case "$home" in
        # ""|"/"|...)`, см. `_FORBIDDEN_HOMES` в `clients/ssh.py`.
        call = ssh._conn.run.await_args
        assert call is not None
        cmd = call.args[0]
        assert "case " in cmd
        assert '""' in cmd
        assert '"/"' in cmd
        assert "exit 1" in cmd
        # mkdir идёт ПОСЛЕ guard'а — не раньше.
        guard_pos = cmd.find("exit 1")
        mkdir_pos = cmd.find('mkdir -p "$home/.ssh"')
        assert guard_pos < mkdir_pos, (
            "exit-guard должен предшествовать mkdir, иначе guard "
            "сработает слишком поздно"
        )
        # SshError с правильным rc — propogate'нут.
        assert exc.value.returncode == 1
