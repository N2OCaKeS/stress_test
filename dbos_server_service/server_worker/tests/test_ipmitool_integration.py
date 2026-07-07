"""Интеграционные тесты power.* / ipmi.rotate_password через ipmitool fallback.

Сценарий: Redfish-probe (HEAD `/redfish/v1/`) проваливается, `get_bmc_client`
отдаёт `IpmitoolClient`, и handler говорит с BMC через subprocess. Здесь
проверяется:

* что handler корректно ходит через `dispatch_power_action` / `dispatch_rotate_user_password`;
* что `_wrap_ipmitool_error` маппит stderr-маркеры в `BMC_AUTH_FAILED` /
  `BMC_UNREACHABLE` / `BMC_REJECTED` / `BMC_TIMEOUT`;
* что argv субпроцесса содержит правильный `chassis power <action>` /
  `user set password <id> <pwd>`;
* что пароль не утекает в audit details (`_runner.run_task` whitelist + mask
  в argv_safe).

Используем те же fixtures (`make_task`, `fetch_task`, `captured_audit`), что и
обычные task-тесты. Probe замокан на False — handler сразу собирает
`IpmitoolClient`; subprocess замокан через `_patch_subprocess`.
"""

from __future__ import annotations

import pytest

from src.core.constants import TaskStatus
from src.tasks import passwords, power
from tests._helpers.subprocess_mocks import (
    make_fake_process as _make_fake_process,
    patch_subprocess as _patch_subprocess,
)


def _force_ipmitool(monkeypatch) -> None:
    """Сбить Redfish-probe в False — `get_bmc_client` отдаст IpmitoolClient.

    `_probe_redfish` — internal helper в `src/clients/__init__.py`; патчим
    его, а не httpx, чтобы не зависеть от конкретного transport-error path'а.
    """
    async def fake_probe(host: str, *, scheme: str = "https") -> bool:
        return False
    monkeypatch.setattr("src.clients._probe_redfish", fake_probe)


def _patch_creds(monkeypatch, module_name: str, **overrides: Any) -> None:
    """Мокнуть `fetch_ipmi_credentials` в `tasks.power` / `tasks.passwords`."""
    base = {
        "controller_id": "ipm_1",
        "kind": "supermicro_x10",
        "endpoint_url": "10.0.0.5",  # raw host без https:// — ipmitool это съест
        "username": "ADMIN",
        "password": "old_password",
        **overrides,
    }

    async def fake_fetch(server_id, target_department_id=None):
        return base

    monkeypatch.setattr(
        f"src.tasks.{module_name}.server_service_client.fetch_ipmi_credentials",
        fake_fetch,
    )


# ── power.on / off / reboot / status через ipmitool ─────────────────────────


class TestPowerOnIpmitool:
    async def test_power_on_succeeds_via_ipmitool(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        tid = await make_task(task_kind="power.on", target_server_id="srv_1")
        _force_ipmitool(monkeypatch)
        _patch_creds(monkeypatch, "power")

        # state поднимается после `chassis power on`
        state = {"value": "off"}

        def proc_factory(args, kwargs):
            argv = args[0]  # ipmitool бинарь
            sub = list(args)
            if "on" in sub:
                state["value"] = "on"
                return _make_fake_process(returncode=0, stdout=b"")
            # chassis power status
            return _make_fake_process(
                returncode=0,
                stdout=f"Chassis Power is {state['value']}\n".encode(),
            )

        calls = _patch_subprocess(monkeypatch, proc_factory)
        await power.power_on.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        assert t.result == {"power_state": "on"}
        # Хотя бы один subprocess-вызов был ipmitool chassis power on
        argvs = [c[0] for c in calls]
        assert any("on" in argv and "power" in argv for argv in argvs)
        assert captured_audit[0]["action"] == "server.power_on"


class TestPowerOffIpmitool:
    async def test_power_off_force_via_ipmitool(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        tid = await make_task(task_kind="power.off", target_server_id="srv_1")
        _force_ipmitool(monkeypatch)
        _patch_creds(monkeypatch, "power")

        state = {"value": "on"}

        def proc_factory(args, kwargs):
            sub = list(args)
            if "off" in sub:
                state["value"] = "off"
                return _make_fake_process(returncode=0, stdout=b"")
            return _make_fake_process(
                returncode=0,
                stdout=f"Chassis Power is {state['value']}\n".encode(),
            )

        _patch_subprocess(monkeypatch, proc_factory)
        await power.power_off.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        assert t.result["power_state"] == "off"
        assert t.result["previous_power_state"] == "on"

    async def test_power_off_ipmitool_is_always_hard(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        """power.off через ipmitool — всегда `chassis power off` (hard).

        Даже если payload содержит `graceful=true` (legacy-клиент), worker
        обязан игнорировать подсказку и слать hard off. `soft` не должно
        появляться.
        """
        tid = await make_task(
            task_kind="power.off",
            target_server_id="srv_1",
            payload={"server_id": "srv_1", "graceful": True},
        )
        _force_ipmitool(monkeypatch)
        _patch_creds(monkeypatch, "power")

        seen_actions: list[str] = []

        def proc_factory(args, kwargs):
            sub = list(args)
            for token in ("on", "off", "cycle", "reset", "soft"):
                if token in sub and "power" in sub:
                    seen_actions.append(token)
                    break
            return _make_fake_process(returncode=0, stdout=b"Chassis Power is off\n")

        _patch_subprocess(monkeypatch, proc_factory)
        await power.power_off.original_func(tid)

        assert "off" in seen_actions
        assert "soft" not in seen_actions


class TestPowerRebootIpmitool:
    async def test_reboot_uses_reset(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        tid = await make_task(task_kind="power.reboot", target_server_id="srv_1")
        _force_ipmitool(monkeypatch)
        _patch_creds(monkeypatch, "power")

        seen_actions: list[str] = []

        def proc_factory(args, kwargs):
            sub = list(args)
            for token in ("on", "off", "cycle", "reset", "soft"):
                if token in sub and "power" in sub:
                    seen_actions.append(token)
                    break
            return _make_fake_process(returncode=0, stdout=b"Chassis Power is on\n")

        _patch_subprocess(monkeypatch, proc_factory)
        await power.power_reboot.original_func(tid)

        # ForceRestart → reset
        assert "reset" in seen_actions
        t = await fetch_task(tid)
        assert t.result["rebooted"] is True

    async def test_reboot_ignores_force_uses_reset(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        tid = await make_task(
            task_kind="power.reboot",
            target_server_id="srv_1",
            payload={"server_id": "srv_1", "force": False},
        )
        _force_ipmitool(monkeypatch)
        _patch_creds(monkeypatch, "power")

        seen: list[str] = []

        def proc_factory(args, kwargs):
            sub = list(args)
            for token in ("on", "off", "cycle", "reset", "soft"):
                if token in sub and "power" in sub:
                    seen.append(token)
                    break
            return _make_fake_process(returncode=0, stdout=b"Chassis Power is on\n")

        _patch_subprocess(monkeypatch, proc_factory)
        await power.power_reboot.original_func(tid)

        # force в payload игнорируется — всё равно reset
        assert "reset" in seen


class TestPowerStatusIpmitool:
    async def test_status_returns_on(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        tid = await make_task(task_kind="power.status", target_server_id="srv_1")
        _force_ipmitool(monkeypatch)
        _patch_creds(monkeypatch, "power")

        proc = _make_fake_process(returncode=0, stdout=b"Chassis Power is on\n")
        _patch_subprocess(monkeypatch, proc)

        await power.power_status.original_func(tid)
        t = await fetch_task(tid)
        # Нет host в payload → ping/ssh неизмеримы; ipmitool отдал on.
        assert t.result == {
            "power_state": "on",
            "source": "bmc",
            "ping_reachable": False,
            "ping_latency_ms": None,
            "ssh_reachable": False,
            "ssh_latency_ms": None,
            "ipmi_power_state": "on",
        }

    async def test_status_returns_off(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        tid = await make_task(task_kind="power.status", target_server_id="srv_1")
        _force_ipmitool(monkeypatch)
        _patch_creds(monkeypatch, "power")

        proc = _make_fake_process(returncode=0, stdout=b"Chassis Power is off\n")
        _patch_subprocess(monkeypatch, proc)

        await power.power_status.original_func(tid)
        t = await fetch_task(tid)
        # Нет host в payload → ping/ssh неизмеримы; ipmitool отдал off.
        assert t.result == {
            "power_state": "off",
            "source": "bmc",
            "ping_reachable": False,
            "ping_latency_ms": None,
            "ssh_reachable": False,
            "ssh_latency_ms": None,
            "ipmi_power_state": "off",
        }


# ── error mapping: ipmitool stderr → BMC_* ──────────────────────────────────


def _patch_no_retry(monkeypatch) -> None:
    from src.tasks import _runner
    async def noop(*args, **kwargs):
        pass
    monkeypatch.setattr(_runner, "_schedule_retry", noop)


async def _make_task_max1(make_task, **kw) -> str:
    from sqlalchemy import update
    from src.db.session import AsyncSessionLocal
    from src.models import Task
    tid = await make_task(**kw)
    async with AsyncSessionLocal() as session:
        await session.execute(update(Task).where(Task.id == tid).values(max_attempts=1))
        await session.commit()
    return tid


class TestIpmitoolErrorMapping:
    async def test_unreachable_stderr_maps_to_bmc_unreachable(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        tid = await _make_task_max1(make_task, task_kind="power.on", target_server_id="srv_1")
        _patch_no_retry(monkeypatch)
        _force_ipmitool(monkeypatch)
        _patch_creds(monkeypatch, "power")

        proc = _make_fake_process(
            returncode=1,
            stderr=b"Error: Unable to establish IPMI v2 / RMCP+ session\n",
        )
        _patch_subprocess(monkeypatch, proc)

        await power.power_on.original_func(tid)
        t = await fetch_task(tid)
        assert t.status == TaskStatus.FAILED
        assert "BMC_UNREACHABLE" in t.last_error

    async def test_auth_failure_maps_to_bmc_auth_failed(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        tid = await _make_task_max1(make_task, task_kind="power.on", target_server_id="srv_1")
        _patch_no_retry(monkeypatch)
        _force_ipmitool(monkeypatch)
        _patch_creds(monkeypatch, "power")

        proc = _make_fake_process(
            returncode=1,
            stderr=b"Error: RAKP 2 message indicates an error : invalid integrity check\n",
        )
        _patch_subprocess(monkeypatch, proc)

        await power.power_on.original_func(tid)
        t = await fetch_task(tid)
        assert t.status == TaskStatus.FAILED
        assert "BMC_AUTH_FAILED" in t.last_error

    async def test_rejected_invalid_state_maps_to_bmc_rejected(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        """rc != 0 + неопознанный stderr → BMC_REJECTED."""
        tid = await _make_task_max1(make_task, task_kind="power.on", target_server_id="srv_1")
        _patch_no_retry(monkeypatch)
        _force_ipmitool(monkeypatch)
        _patch_creds(monkeypatch, "power")

        proc = _make_fake_process(
            returncode=1,
            stderr=b"Error: chassis already powered on\n",
        )
        _patch_subprocess(monkeypatch, proc)

        await power.power_on.original_func(tid)
        t = await fetch_task(tid)
        assert t.status == TaskStatus.FAILED
        assert "BMC_REJECTED" in t.last_error

    async def test_binary_not_found_maps_to_bmc_unreachable(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        # power.on (action) — на нём отсутствие ipmitool по-прежнему валит
        # задачу с BMC_UNREACHABLE. power.status теперь сознательно уходит в
        # reachability-fallback вместо FAILED (см. test_power_reachability_fallback).
        tid = await _make_task_max1(make_task, task_kind="power.on", target_server_id="srv_1")
        _patch_no_retry(monkeypatch)
        _force_ipmitool(monkeypatch)
        _patch_creds(monkeypatch, "power")

        async def fake_exec(*args, **kwargs):
            raise FileNotFoundError("ipmitool: command not found")

        monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

        await power.power_on.original_func(tid)
        t = await fetch_task(tid)
        assert t.status == TaskStatus.FAILED
        assert "BMC_UNREACHABLE" in t.last_error


# ── ipmi.rotate_password через ipmitool ─────────────────────────────────────


def _ipmitool_rotate_proc_factory(state: str = "on"):
    """Фабрика subprocess'ов для verify-then-submit ipmi.rotate_password.

    Возвращает callable `(args, kwargs) -> Process`, которая:
      * `user set password ...` → returncode=0, пустой stdout (apply OK);
      * `chassis power status` → returncode=0, "Chassis Power is on" (verify OK);
      * прочее → returncode=0, пустой stdout.
    """
    def factory(args, kwargs):
        argv = list(args)
        if "status" in argv and "power" in argv:
            return _make_fake_process(
                returncode=0,
                stdout=f"Chassis Power is {state}\n".encode(),
            )
        return _make_fake_process(returncode=0)
    return factory


class TestIpmiRotatePasswordIpmitool:
    async def test_full_flow_storage_then_user_set_password(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        tid = await make_task(
            task_kind="ipmi.rotate_password",
            target_server_id="srv_1",
            payload={"server_id": "srv_1"},
        )
        _force_ipmitool(monkeypatch)
        _patch_creds(monkeypatch, "passwords")

        submit_calls: list[tuple] = []
        async def fake_submit(controller_id, new_password, rotated_at, target_department_id=None, verified_at=None):
            submit_calls.append((controller_id, new_password, rotated_at, verified_at))
            return {"rotated_at": "2026-05-21T17:00:00Z"}

        monkeypatch.setattr(
            "src.tasks.passwords.server_service_client.submit_rotated_ipmi_password",
            fake_submit,
        )

        calls = _patch_subprocess(monkeypatch, _ipmitool_rotate_proc_factory())

        await passwords.ipmi_rotate_password.original_func(tid)
        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        assert t.result["controller_rotated"] is True
        assert t.result["password_rotated_at"] == "2026-05-21T17:00:00Z"
        # submit вызван один раз и с `verified_at` (verify прошёл).
        assert len(submit_calls) == 1
        new_password = submit_calls[0][1]
        assert submit_calls[0][3] is not None, "verified_at должен быть передан"
        argv_with_user = next(
            (c[0] for c in calls if "user" in c[0] and "set" in c[0] and "password" in c[0]),
            None,
        )
        assert argv_with_user is not None, "ipmitool user set password не вызывался"
        # `user set password <id> <newpass>` — пароль в argv (это known
        # exposure через /proc/<pid>/cmdline у ipmitool'а, обходить нечем).
        assert new_password in argv_with_user
        # verify через `chassis power status` — этот вызов идёт под новым
        # паролем через `IPMI_PASSWORD` env (а не argv). Найдём verify-вызов
        # и убедимся, что env содержит новый пароль.
        verify_calls = [c for c in calls if "status" in c[0] and "power" in c[0]]
        assert len(verify_calls) == 1, "должен быть ровно один verify-вызов"
        env_for_verify = verify_calls[0][1].get("env") or {}
        assert env_for_verify.get("IPMI_PASSWORD") == new_password, (
            "verify должен идти с новым паролем (через IPMI_PASSWORD env)"
        )
        assert captured_audit[0]["action"] == "ipmi_controller.password_rotate"
        assert captured_audit[0]["target_type"] == "ipmi_controller"

    async def test_user_id_override_passed_to_ipmitool(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        tid = await make_task(
            task_kind="ipmi.rotate_password",
            target_server_id="srv_1",
            payload={"server_id": "srv_1", "user_id": 4},
        )
        _force_ipmitool(monkeypatch)
        _patch_creds(monkeypatch, "passwords")

        async def fake_submit(controller_id, new_password, rotated_at, target_department_id=None, verified_at=None):
            return {"rotated_at": "x"}
        monkeypatch.setattr(
            "src.tasks.passwords.server_service_client.submit_rotated_ipmi_password",
            fake_submit,
        )

        calls = _patch_subprocess(monkeypatch, _ipmitool_rotate_proc_factory())
        await passwords.ipmi_rotate_password.original_func(tid)

        argv_with_user = next(
            (c[0] for c in calls if "user" in c[0] and "set" in c[0]),
            None,
        )
        assert argv_with_user is not None
        # `user set password <user_id> <newpass>` — user_id-аргумент на +3 от 'user'
        idx = argv_with_user.index("user")
        assert argv_with_user[idx + 3] == "4"

    async def test_bmc_apply_failure_skips_submit(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        """ipmitool user set password упал с AUTH → submit НЕ вызывается,
        task FAILED с `BMC_AUTH_FAILED`."""
        tid = await _make_task_max1(
            make_task,
            task_kind="ipmi.rotate_password",
            target_server_id="srv_1",
            payload={"server_id": "srv_1"},
        )
        _patch_no_retry(monkeypatch)
        _force_ipmitool(monkeypatch)
        _patch_creds(monkeypatch, "passwords")

        submit_calls: list = []
        async def fake_submit(controller_id, new_password, rotated_at, target_department_id=None, verified_at=None):
            submit_calls.append(controller_id)
            return {"rotated_at": "ok"}
        monkeypatch.setattr(
            "src.tasks.passwords.server_service_client.submit_rotated_ipmi_password",
            fake_submit,
        )

        proc = _make_fake_process(
            returncode=1,
            stderr=b"Error: RAKP 2 message indicates an error\n",
        )
        _patch_subprocess(monkeypatch, proc)

        await passwords.ipmi_rotate_password.original_func(tid)
        t = await fetch_task(tid)
        assert t.status == TaskStatus.FAILED
        assert "BMC_AUTH_FAILED" in t.last_error
        assert submit_calls == [], "submit must not be called if BMC apply failed"

    async def test_verify_failure_skips_submit(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        """ipmitool user set password прошёл, но verify (chassis power status)
        под новым паролем вернул AUTH-ошибку → submit НЕ вызывается, task
        FAILED с `BMC_VERIFY_AFTER_ROTATE_FAILED`."""
        tid = await _make_task_max1(
            make_task,
            task_kind="ipmi.rotate_password",
            target_server_id="srv_1",
            payload={"server_id": "srv_1"},
        )
        _patch_no_retry(monkeypatch)
        _force_ipmitool(monkeypatch)
        _patch_creds(monkeypatch, "passwords")

        submit_calls: list = []
        async def fake_submit(controller_id, new_password, rotated_at, target_department_id=None, verified_at=None):
            submit_calls.append(controller_id)
            return {"rotated_at": "ok"}
        monkeypatch.setattr(
            "src.tasks.passwords.server_service_client.submit_rotated_ipmi_password",
            fake_submit,
        )

        def proc_factory(args, kwargs):
            argv = list(args)
            if "status" in argv and "power" in argv:
                # Verify-шаг — BMC отверг новый пароль.
                return _make_fake_process(
                    returncode=1,
                    stderr=b"Error: RAKP 2 message indicates an error\n",
                )
            # Apply — OK.
            return _make_fake_process(returncode=0)

        _patch_subprocess(monkeypatch, proc_factory)

        await passwords.ipmi_rotate_password.original_func(tid)
        t = await fetch_task(tid)
        assert t.status == TaskStatus.FAILED
        assert "BMC_VERIFY_AFTER_ROTATE_FAILED" in t.last_error
        assert submit_calls == [], "submit must not be called if verify failed"

    async def test_plaintext_password_not_in_audit(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        tid = await make_task(
            task_kind="ipmi.rotate_password",
            target_server_id="srv_1",
            payload={"server_id": "srv_1"},
        )
        _force_ipmitool(monkeypatch)
        _patch_creds(monkeypatch, "passwords")

        captured = {}
        async def fake_submit(controller_id, new_password, rotated_at, target_department_id=None, verified_at=None):
            captured["pwd"] = new_password
            return {"rotated_at": "x"}
        monkeypatch.setattr(
            "src.tasks.passwords.server_service_client.submit_rotated_ipmi_password",
            fake_submit,
        )

        _patch_subprocess(monkeypatch, _ipmitool_rotate_proc_factory())

        await passwords.ipmi_rotate_password.original_func(tid)
        # Пароль не в audit
        assert captured["pwd"] not in str(captured_audit)

    async def test_retry_reuses_stashed_password(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        """Retry IPMI rotate использует ТОТ ЖЕ пароль, что и первая попытка.

        Сценарий бага: при retry'е `_impl` запускался заново, генерил новый
        пароль через `_generate_password`, перезаписывал storage. Если BMC
        принял попытку 1, но ответ не дошёл, попытка 2 ставила другой пароль
        и в storage, и в BMC — финальный fail оставлял в storage пароль,
        который НИКОГДА не подтверждался на BMC.

        Здесь моделируем «попытка 1 уже отработала, stash в Redis есть»:
        предзаполняем stash вручную, дёргаем handler, видим что
        `submit_rotated_ipmi_password` приходит с тем же паролем — генерации
        нового не произошло.
        """
        from src.tasks import passwords

        tid = await make_task(
            task_kind="ipmi.rotate_password",
            target_server_id="srv_1",
            payload={"server_id": "srv_1"},
        )
        _force_ipmitool(monkeypatch)
        _patch_creds(monkeypatch, "passwords")

        stashed_pwd = "stashed_password_from_attempt_1"
        await passwords._store_ipmi_rotate_password(tid, stashed_pwd)

        submit_calls: list[str] = []
        async def fake_submit(controller_id, new_password, rotated_at, target_department_id=None, verified_at=None):
            submit_calls.append(new_password)
            return {"rotated_at": "x"}
        monkeypatch.setattr(
            "src.tasks.passwords.server_service_client.submit_rotated_ipmi_password",
            fake_submit,
        )

        _patch_subprocess(monkeypatch, _ipmitool_rotate_proc_factory())

        await passwords.ipmi_rotate_password.original_func(tid)

        # submit получил пароль из stash'а, не новый CSPRNG.
        assert submit_calls == [stashed_pwd]

        # После успеха stash вычищен.
        assert (await passwords._read_ipmi_rotate_state(tid))[0] is None

    async def test_stash_helpers_roundtrip(self, monkeypatch):
        """Базовый roundtrip Redis-stash'а пароля ротации: write → read → delete."""
        from src.tasks import passwords

        # Используем task_id-fake, не пересекающийся с реальными task'ами.
        fake_tid = "tsk_stash_roundtrip_test"

        # На всякий случай чистим, если предыдущий прогон оставил ключ.
        await passwords._delete_ipmi_rotate_password(fake_tid)

        # Изначально ключа нет.
        assert (await passwords._read_ipmi_rotate_state(fake_tid))[0] is None

        await passwords._store_ipmi_rotate_password(fake_tid, "secret_42")
        assert (await passwords._read_ipmi_rotate_state(fake_tid))[0] == "secret_42"

        await passwords._delete_ipmi_rotate_password(fake_tid)
        assert (await passwords._read_ipmi_rotate_state(fake_tid))[0] is None


# ── unit tests для wrap_ipmitool_error ───────────────────────────────────────


class TestWrapIpmitoolError:
    """Unit-coverage чистого маппера, без BD/runner."""

    def test_timeout_subclass_maps_to_bmc_timeout(self):
        from src.clients.ipmitool import IpmitoolTimeout
        from src.tasks._bmc_errors import wrap_bmc_error

        exc = IpmitoolTimeout(
            returncode=-1, stderr="timed out", argv_safe=["ipmitool", "..."],
            message="ipmitool timeout",
        )
        wrapped = wrap_bmc_error("power_on", exc)
        assert wrapped.error_code == "BMC_TIMEOUT"
        assert wrapped.details["transport"] == "ipmitool"

    def test_binary_not_found_maps_to_bmc_unreachable(self):
        from src.clients.ipmitool import IpmitoolError
        from src.tasks._bmc_errors import wrap_bmc_error

        exc = IpmitoolError(
            returncode=-1, stderr="errno 2", argv_safe=["ipmitool", "..."],
            message="ipmitool binary not found",
        )
        wrapped = wrap_bmc_error("power_on", exc)
        assert wrapped.error_code == "BMC_UNREACHABLE"

    def test_rakp_stderr_maps_to_auth_failed(self):
        from src.clients.ipmitool import IpmitoolError
        from src.tasks._bmc_errors import wrap_bmc_error

        exc = IpmitoolError(
            returncode=1,
            stderr="Error: RAKP 2 message indicates an error",
            argv_safe=["ipmitool", "-P", "***"],
            message="failed",
        )
        wrapped = wrap_bmc_error("power_on", exc)
        assert wrapped.error_code == "BMC_AUTH_FAILED"
        # argv_safe пробрасывается в details — пароля там нет, только '***'
        assert "***" in wrapped.details["argv_safe"]

    def test_unable_to_establish_maps_to_unreachable(self):
        from src.clients.ipmitool import IpmitoolError
        from src.tasks._bmc_errors import wrap_bmc_error

        exc = IpmitoolError(
            returncode=1,
            stderr="Error: Unable to establish IPMI v2 / RMCP+ session",
            argv_safe=["ipmitool"],
        )
        wrapped = wrap_bmc_error("power_on", exc)
        assert wrapped.error_code == "BMC_UNREACHABLE"

    def test_generic_rc1_maps_to_rejected(self):
        from src.clients.ipmitool import IpmitoolError
        from src.tasks._bmc_errors import wrap_bmc_error

        exc = IpmitoolError(
            returncode=1,
            stderr="Some other error",
            argv_safe=["ipmitool"],
        )
        wrapped = wrap_bmc_error("power_on", exc)
        assert wrapped.error_code == "BMC_REJECTED"

    def test_unsupported_exception_type_raises(self):
        from src.tasks._bmc_errors import wrap_bmc_error

        with pytest.raises(TypeError):
            wrap_bmc_error("power_on", KeyError("nope"))

    def test_value_error_unsupported_action_mapped(self):
        """ValueError из dispatch_power_action → BMC_UNSUPPORTED_ACTION."""
        from src.tasks._bmc_errors import wrap_bmc_error

        exc = ValueError("BMC_UNSUPPORTED_ACTION: 'PushPowerButton' has no ipmitool equivalent")
        wrapped = wrap_bmc_error("power_on", exc)
        assert wrapped.error_code == "BMC_UNSUPPORTED_ACTION"
        assert wrapped.details["transport"] == "dispatch"

    def test_runtime_error_client_incompatible_mapped(self):
        """RuntimeError из dispatch_* → BMC_CLIENT_INCOMPATIBLE."""
        from src.tasks._bmc_errors import wrap_bmc_error

        exc = RuntimeError(
            "BMC_CLIENT_INCOMPATIBLE: FakeClient has no `power_action` nor `chassis_power_action`"
        )
        wrapped = wrap_bmc_error("power_on", exc)
        assert wrapped.error_code == "BMC_CLIENT_INCOMPATIBLE"
        assert wrapped.details["transport"] == "dispatch"

    def test_runtime_error_no_prefix_falls_back_to_error(self):
        from src.tasks._bmc_errors import wrap_bmc_error

        wrapped = wrap_bmc_error("power_on", RuntimeError("неизвестно что"))
        assert wrapped.error_code == "BMC_ERROR"
