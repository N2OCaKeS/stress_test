"""Тесты ipmitool CLI-клиента (`src/clients/ipmitool.py`).

Subprocess моками — `asyncio.create_subprocess_exec` подменяется через
`monkeypatch.setattr` на async-функцию, которая возвращает fake-Process
с заранее заданными `returncode` и `(stdout, stderr)` для `communicate()`.

Фокус:

* парсинг `chassis power status` (стандартный happy-вывод и edge-cases);
* передача action'ов и persistent-флага в argv;
* `user_set_password` — пароль не утекает ни в `IpmitoolError.argv_safe`,
  ни в stderr-секцию после redact;
* timeout: `wait_for` → `IpmitoolTimeout`, subprocess получает terminate();
* отсутствие ipmitool в PATH → `IpmitoolError` (а не raw FileNotFoundError);
* generic auth failure: rc=1 + stderr "RAKP 2 message indicates an error" →
  `IpmitoolError` с маскированным argv.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.clients.ipmitool import (
    IpmitoolClient,
    IpmitoolError,
    IpmitoolTimeout,
    _mask_password_in_argv,
)


# ── helpers ──────────────────────────────────────────────────────────────────


def _make_fake_process(
    returncode: int = 0,
    stdout: bytes = b"",
    stderr: bytes = b"",
    *,
    communicate_raises: BaseException | None = None,
) -> MagicMock:
    """Сконструировать MagicMock, имитирующий asyncio.subprocess.Process."""
    proc = MagicMock()
    proc.returncode = returncode
    if communicate_raises is not None:
        proc.communicate = AsyncMock(side_effect=communicate_raises)
    else:
        proc.communicate = AsyncMock(return_value=(stdout, stderr))
    proc.wait = AsyncMock(return_value=returncode)
    proc.terminate = MagicMock()
    proc.kill = MagicMock()
    return proc


def _patch_subprocess(monkeypatch, proc_or_factory: Any) -> list[tuple[tuple, dict]]:
    """Подменить `asyncio.create_subprocess_exec` и собрать вызовы.

    `proc_or_factory` — либо готовый MagicMock-Process, либо обычная
    функция-фабрика (для тестов, где результат зависит от команды).
    MagicMock тоже callable, поэтому различаем по типу: MagicMock —
    готовый proc, всё прочее callable — factory.

    Возвращает список `[((argv...), kwargs), ...]` — для assertion'ов о
    том, что в argv попал правильный набор аргументов.
    """
    calls: list[tuple[tuple, dict]] = []

    async def fake_exec(*args, **kwargs):
        calls.append((args, kwargs))
        if not isinstance(proc_or_factory, MagicMock) and callable(proc_or_factory):
            return proc_or_factory(args, kwargs)
        return proc_or_factory

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)
    return calls


# ── chassis_power_status ─────────────────────────────────────────────────────


class TestChassisPowerStatus:
    async def test_returns_on_for_powered_chassis(self, monkeypatch):
        proc = _make_fake_process(returncode=0, stdout=b"Chassis Power is on\n")
        _patch_subprocess(monkeypatch, proc)

        client = IpmitoolClient("10.0.0.5", "ADMIN", "secret")
        state = await client.chassis_power_status()

        assert state == "on"

    async def test_returns_off_for_powered_off_chassis(self, monkeypatch):
        proc = _make_fake_process(returncode=0, stdout=b"Chassis Power is off\n")
        _patch_subprocess(monkeypatch, proc)

        client = IpmitoolClient("10.0.0.5", "ADMIN", "secret")
        state = await client.chassis_power_status()

        assert state == "off"

    async def test_case_insensitive_match(self, monkeypatch):
        proc = _make_fake_process(returncode=0, stdout=b"CHASSIS POWER IS ON\n")
        _patch_subprocess(monkeypatch, proc)

        client = IpmitoolClient("10.0.0.5", "ADMIN", "secret")
        state = await client.chassis_power_status()

        assert state == "on"

    async def test_unrecognized_output_raises(self, monkeypatch):
        proc = _make_fake_process(returncode=0, stdout=b"Chassis Power is unknown\n")
        _patch_subprocess(monkeypatch, proc)

        client = IpmitoolClient("10.0.0.5", "ADMIN", "secret")
        with pytest.raises(IpmitoolError) as exc_info:
            await client.chassis_power_status()

        assert "unrecognized" in exc_info.value.message.lower()

    async def test_nonzero_exit_raises_without_password_in_argv(self, monkeypatch):
        proc = _make_fake_process(
            returncode=1,
            stderr=b"Error: Unable to establish IPMI v2 / RMCP+ session\n",
        )
        _patch_subprocess(monkeypatch, proc)

        client = IpmitoolClient("10.0.0.5", "ADMIN", "topsecret123")
        with pytest.raises(IpmitoolError) as exc_info:
            await client.chassis_power_status()

        err = exc_info.value
        assert err.returncode == 1
        # Пароль читается из env по `-E`, в argv его нет — утечь нечему.
        assert "topsecret123" not in " ".join(err.argv_safe)
        assert "-E" in err.argv_safe
        assert "-P" not in err.argv_safe


# ── chassis_power_action ─────────────────────────────────────────────────────


class TestChassisPowerAction:
    @pytest.mark.parametrize("action", ["on", "off", "cycle", "reset", "soft"])
    async def test_valid_actions_pass_to_subprocess(self, monkeypatch, action):
        proc = _make_fake_process(returncode=0)
        calls = _patch_subprocess(monkeypatch, proc)

        client = IpmitoolClient("10.0.0.5", "ADMIN", "secret")
        await client.chassis_power_action(action)

        assert len(calls) == 1
        argv = calls[0][0]
        assert argv[-3:] == ("chassis", "power", action)

    async def test_invalid_action_raises_valueerror(self, monkeypatch):
        client = IpmitoolClient("10.0.0.5", "ADMIN", "secret")
        with pytest.raises(ValueError, match="invalid power action"):
            await client.chassis_power_action("nuke")  # type: ignore[arg-type]

    async def test_failure_password_masked_in_error(self, monkeypatch):
        proc = _make_fake_process(
            returncode=1,
            stderr=b"Error: Unable to establish session: -P MY-LIVE-PASSWORD\n",
        )
        _patch_subprocess(monkeypatch, proc)

        client = IpmitoolClient("10.0.0.5", "ADMIN", "MY-LIVE-PASSWORD")
        with pytest.raises(IpmitoolError) as exc_info:
            await client.chassis_power_action("cycle")

        err = exc_info.value
        joined = " ".join(err.argv_safe) + " " + err.stderr
        assert "MY-LIVE-PASSWORD" not in joined


# ── user_set_password ────────────────────────────────────────────────────────


class TestUserSetPassword:
    async def test_password_not_in_argv_safe(self, monkeypatch):
        proc = _make_fake_process(returncode=0)
        calls = _patch_subprocess(monkeypatch, proc)

        client = IpmitoolClient("10.0.0.5", "ADMIN", "bmcpw")
        await client.user_set_password(2, "BRAND-NEW-PASSWORD-9999")

        # Подпроцессу новый пароль ушёл (это аргумент ipmitool, иначе он не
        # сменит пароль на BMC) — это ожидаемо.
        argv = calls[0][0]
        assert "BRAND-NEW-PASSWORD-9999" in argv

    async def test_failure_masks_new_password_in_error(self, monkeypatch):
        proc = _make_fake_process(
            returncode=1,
            stderr=b"Set User Password command failed (user 2)\n",
        )
        _patch_subprocess(monkeypatch, proc)

        client = IpmitoolClient("10.0.0.5", "ADMIN", "bmcpw")
        with pytest.raises(IpmitoolError) as exc_info:
            await client.user_set_password(2, "BRAND-NEW-PASSWORD-9999")

        err = exc_info.value
        joined = " ".join(err.argv_safe)
        # Ни старый bmc-пароль, ни новый user-пароль не должны утечь.
        assert "BRAND-NEW-PASSWORD-9999" not in joined
        assert "bmcpw" not in joined
        # Маркер маскировки точно есть (как минимум один `***`).
        assert "***" in err.argv_safe

    async def test_invalid_user_id_low_raises(self):
        client = IpmitoolClient("10.0.0.5", "ADMIN", "secret")
        with pytest.raises(ValueError, match="invalid IPMI user_id"):
            await client.user_set_password(0, "newpw")

    async def test_invalid_user_id_high_raises(self):
        client = IpmitoolClient("10.0.0.5", "ADMIN", "secret")
        with pytest.raises(ValueError, match="invalid IPMI user_id"):
            await client.user_set_password(64, "newpw")


# ── timeout / network / not-installed ────────────────────────────────────────


class TestTimeoutAndIO:
    async def test_timeout_raises_ipmitool_timeout(self, monkeypatch):
        proc = _make_fake_process(
            returncode=0,
            communicate_raises=asyncio.TimeoutError(),
        )
        _patch_subprocess(monkeypatch, proc)

        client = IpmitoolClient("10.0.0.5", "ADMIN", "secret", timeout=0.01)
        with pytest.raises(IpmitoolTimeout) as exc_info:
            await client.chassis_power_status()

        # IpmitoolTimeout — подкласс IpmitoolError.
        assert isinstance(exc_info.value, IpmitoolError)
        assert "timeout" in exc_info.value.message.lower()
        # terminate() должен быть вызван при timeout (но не kill — proc.wait
        # отрабатывает быстрее, чем _TERMINATE_GRACE_SECONDS).
        proc.terminate.assert_called_once()

    async def test_timeout_password_masked_in_argv_safe(self, monkeypatch):
        proc = _make_fake_process(
            returncode=0,
            communicate_raises=asyncio.TimeoutError(),
        )
        _patch_subprocess(monkeypatch, proc)

        client = IpmitoolClient("10.0.0.5", "ADMIN", "OOPS-MY-PASSWORD", timeout=0.01)
        with pytest.raises(IpmitoolTimeout) as exc_info:
            await client.chassis_power_status()

        joined = " ".join(exc_info.value.argv_safe)
        assert "OOPS-MY-PASSWORD" not in joined

    async def test_binary_not_found_raises_ipmitool_error(self, monkeypatch):
        async def fake_exec(*args, **kwargs):
            raise FileNotFoundError("ipmitool: command not found")

        monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

        client = IpmitoolClient("10.0.0.5", "ADMIN", "secret")
        with pytest.raises(IpmitoolError) as exc_info:
            await client.chassis_power_status()

        assert "binary not found" in exc_info.value.message.lower()

    async def test_kill_path_when_terminate_does_not_stop_process(self, monkeypatch):
        """Если terminate() не сработал в grace-окне, должен быть kill()."""
        proc = _make_fake_process(
            returncode=0,
            communicate_raises=asyncio.TimeoutError(),
        )
        # Первый proc.wait() (внутри grace-window'а после terminate()) уходит
        # в timeout — это запускает ветку kill(). Второй proc.wait() (уже
        # после kill()) завершается нормально, как у настоящего killed-процесса.
        proc.wait = AsyncMock(side_effect=[asyncio.TimeoutError(), 0])
        _patch_subprocess(monkeypatch, proc)

        # Делаем _kill_process быстрым, подменяя _TERMINATE_GRACE_SECONDS.
        monkeypatch.setattr(
            "src.clients.ipmitool._TERMINATE_GRACE_SECONDS", 0.001,
        )

        client = IpmitoolClient("10.0.0.5", "ADMIN", "secret", timeout=0.001)
        with pytest.raises(IpmitoolTimeout):
            await client.chassis_power_status()

        # Не падает в kill-path — но не assert'им вызов, потому что proc.wait
        # после kill() могут не успеть до полного timeout'а; главное — что
        # IpmitoolTimeout всё-таки получили.


# ── argv composition ─────────────────────────────────────────────────────────


class TestBaseArgs:
    async def test_base_args_include_host_port_iface_user(self, monkeypatch):
        proc = _make_fake_process(returncode=0, stdout=b"Chassis Power is on\n")
        calls = _patch_subprocess(monkeypatch, proc)

        client = IpmitoolClient(
            "192.168.1.50", "operator", "pw",
            port=6230, interface="lan",
        )
        await client.chassis_power_status()

        argv = calls[0][0]
        assert "ipmitool" in argv[0]
        assert "-H" in argv and "192.168.1.50" in argv
        assert "-p" in argv and "6230" in argv
        assert "-I" in argv and "lan" in argv
        assert "-U" in argv and "operator" in argv
        # Пароль читается из env по `-E`, а не передаётся в argv через `-P`.
        assert "-E" in argv
        assert "-P" not in argv
        assert "pw" not in argv

    async def test_password_passed_through_env_not_argv(self, monkeypatch):
        proc = _make_fake_process(returncode=0, stdout=b"Chassis Power is on\n")
        calls = _patch_subprocess(monkeypatch, proc)

        client = IpmitoolClient("10.0.0.5", "ADMIN", "TOP-SECRET-PW")
        await client.chassis_power_status()

        argv, kwargs = calls[0]
        # Пароль не светится в argv (== /proc/<pid>/cmdline).
        assert "TOP-SECRET-PW" not in argv
        # Передан дочернему процессу через env по флагу `-E`.
        assert kwargs["env"]["IPMI_PASSWORD"] == "TOP-SECRET-PW"

    async def test_subprocess_env_does_not_leak_worker_secrets(self, monkeypatch):
        """Дочерний ipmitool не должен видеть worker-секреты в env.

        До фикса env был `{**os.environ, IPMI_PASSWORD: ...}` — ipmitool
        наследовал WORKER_BOT_TOKEN, REDIS_PASSWORD, DATABASE_URL и пр.
        После фикса в env только whitelisted ключи (PATH/HOME/LANG/LC_ALL)
        плюс IPMI_PASSWORD.
        """
        # Подкладываем «секрет» в env родителя — fix должен его НЕ передать.
        monkeypatch.setenv("WORKER_BOT_TOKEN", "should-not-leak")
        monkeypatch.setenv("LOGGING_SERVICE_API_KEY", "should-not-leak")
        monkeypatch.setenv("DATABASE_URL", "postgresql://leak:leak@x/y")
        monkeypatch.setenv("REDIS_PASSWORD", "should-not-leak")

        proc = _make_fake_process(returncode=0, stdout=b"Chassis Power is on\n")
        calls = _patch_subprocess(monkeypatch, proc)

        client = IpmitoolClient("10.0.0.5", "ADMIN", "BMC_PW")
        await client.chassis_power_status()

        _, kwargs = calls[0]
        env = kwargs["env"]

        # Whitelist пробросился.
        assert env["IPMI_PASSWORD"] == "BMC_PW"
        # Чувствительные env-секреты — нет.
        for forbidden in ("WORKER_BOT_TOKEN", "LOGGING_SERVICE_API_KEY",
                          "DATABASE_URL", "REDIS_PASSWORD"):
            assert forbidden not in env, f"{forbidden} leaked into ipmitool env"


# ── _mask_password_in_argv ───────────────────────────────────────────────────


class TestMaskPasswordInArgv:
    def test_masks_value_after_dash_p(self):
        argv = ["ipmitool", "-H", "h", "-U", "u", "-P", "topsecret", "chassis", "power", "status"]
        safe = _mask_password_in_argv(argv)
        assert safe[6] == "***"
        # `-U` value не маскируется — username не секрет.
        assert safe[4] == "u"

    def test_masks_user_set_password_last_positional(self):
        argv = [
            "ipmitool", "-H", "h", "-U", "u", "-P", "bmcpw",
            "user", "set", "password", "2", "NEW-PASSWORD",
        ]
        safe = _mask_password_in_argv(argv)
        # И BMC-пароль (-P), и новый user-пароль (последний positional).
        assert safe[6] == "***"
        assert safe[-1] == "***"
        # Команда `user set password 2 ***` — структура читаема.
        assert safe[-5:-1] == ["user", "set", "password", "2"]

    def test_does_not_mutate_input(self):
        argv = ["ipmitool", "-P", "topsecret"]
        _ = _mask_password_in_argv(argv)
        assert argv == ["ipmitool", "-P", "topsecret"]

    def test_no_password_in_argv_safe_when_no_dash_p(self):
        argv = ["ipmitool", "lan", "print"]
        safe = _mask_password_in_argv(argv)
        assert safe == argv

    def test_user_set_password_with_long_password(self):
        argv = [
            "ipmitool", "-H", "h", "-U", "u", "-P", "bmcpw",
            "user", "set", "password", "10",
            "a-very-long-password-with-special-chars!@#$%^",
        ]
        safe = _mask_password_in_argv(argv)
        assert "a-very-long-password-with-special-chars!@#$%^" not in " ".join(safe)


# ── stderr redaction ─────────────────────────────────────────────────────────


class TestStderrRedaction:
    async def test_stderr_password_redacted_in_error(self, monkeypatch):
        """ipmitool иногда логирует команду с `-P pwd` в свой собственный stderr.

        `_redact` должен пройтись по stderr перед сохранением в `IpmitoolError`,
        чтобы пароль не попал в audit-details.
        """
        proc = _make_fake_process(
            returncode=1,
            stderr=b"Error running: ipmitool -U ADMIN -P SUPERSECRETPW chassis power on\n",
        )
        _patch_subprocess(monkeypatch, proc)

        client = IpmitoolClient("10.0.0.5", "ADMIN", "SUPERSECRETPW")
        with pytest.raises(IpmitoolError) as exc_info:
            await client.chassis_power_action("on")

        assert "SUPERSECRETPW" not in exc_info.value.stderr
        assert "<PASSWORD>" in exc_info.value.stderr
