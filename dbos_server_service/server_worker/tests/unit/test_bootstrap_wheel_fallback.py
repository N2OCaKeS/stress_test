"""Unit-тесты sudo→wheel fallback в `SshClient.bootstrap_management_user`.

При SSH_USERADD_FAILED/SSH_USERMOD_FAILED для группы "sudo" (не существует на
дистрибутиве — например, RHEL/CentOS) bootstrap автоматически повторяет
попытку с группой "wheel". Если обе группы не работают — поднимается
последнее исключение.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, call

import asyncssh
import pytest

from src.clients.ssh import SshClient, SshError


_PUBKEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAItest dbos"


def _run_result(stdout="", stderr="", rc=0):
    res = MagicMock()
    res.stdout = stdout
    res.stderr = stderr
    res.exit_status = rc
    return res


def _conn(run_results):
    conn = MagicMock(spec=asyncssh.SSHClientConnection)
    conn.close = MagicMock()
    conn.wait_closed = AsyncMock()
    conn.run = AsyncMock(side_effect=list(run_results))
    return conn


class TestBootstrapWheelFallback:
    async def test_sudo_succeeds_no_wheel_attempt(self, monkeypatch):
        # Стандартный путь: sudo-группа есть (Debian/Astra) — wheel не пробуем.
        # getent (not found rc=2) → внутренний user_exists False → пропускаем
        # pre-check id -nG → useradd rc=0 → sudoers → authorized_keys
        conn = _conn([
            _run_result("", "", 2),  # user_exists pre-check (rc=2)
            _run_result("", "", 2),  # getent внутри create_user (rc=2)
            _run_result("", "", 0),  # useradd -G sudo
            _run_result("", "", 0),  # sudoers
            _run_result("", "", 0),  # authorized_keys
        ])
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=conn))

        async with SshClient("h", "boot", "pwd") as ssh:
            await ssh.bootstrap_management_user("dbos", _PUBKEY)

        # useradd вызван ровно один раз с "-G sudo".
        useradd_calls = [
            c for c in conn.run.await_args_list
            if "useradd" in (c.args[0] if c.args else "")
        ]
        assert len(useradd_calls) == 1
        assert "-G sudo" in useradd_calls[0].args[0]

    async def test_sudo_group_missing_falls_back_to_wheel(self, monkeypatch):
        # sudo-группы нет (RHEL): useradd с -G sudo → rc=6 ("group 'sudo' does
        # not exist") → retry с wheel → успех.
        # Последовательность вызовов:
        #   pre-check: user_exists rc=2 (нет юзера)
        #   attempt-1: getent(rc=2) → useradd -G sudo(rc=6 error)
        #     → post-fail user_exists rc=2 (юзер не создан) → пропускаем
        #     id -nG → retry
        #   attempt-2: getent(rc=2) → useradd -G wheel(rc=0) → sudoers → auth_keys
        conn = _conn([
            _run_result("", "", 2),   # user_exists pre-check
            _run_result("", "", 2),   # getent внутри create_user попытка 1
            _run_result("", "group 'sudo' does not exist", 6),  # useradd sudo fail
            _run_result("", "", 2),   # post-fail user_exists (юзер не создан)
            _run_result("", "", 2),   # getent внутри create_user попытка 2
            _run_result("", "", 0),   # useradd -G wheel success
            _run_result("", "", 0),   # sudoers
            _run_result("", "", 0),   # authorized_keys
        ])
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=conn))

        async with SshClient("h", "boot", "pwd") as ssh:
            await ssh.bootstrap_management_user("dbos", _PUBKEY)

        # Два вызова useradd: первый с sudo, второй с wheel.
        useradd_calls = [
            c for c in conn.run.await_args_list
            if "useradd" in (c.args[0] if c.args else "")
        ]
        assert len(useradd_calls) == 2
        assert "-G sudo" in useradd_calls[0].args[0]
        assert "-G wheel" in useradd_calls[1].args[0]

    async def test_usermod_fail_on_sudo_falls_back_to_wheel(self, monkeypatch):
        # Пользователь уже существует, но не в sudo/wheel (например, прошлая
        # попытка bootstrap'а упала после useradd с пустым -G).
        # pre-check: user_exists=True → id -nG показывает "dbos" (без sudo/wheel)
        # → попытка 1: usermod -G sudo → fail
        #   → post-fail id -nG показывает "dbos" → retry
        # → попытка 2: usermod -G wheel → success
        conn = _conn([
            _run_result("dbos:x:1001:", "", 0),         # user_exists pre-check (есть)
            _run_result("dbos\n", "", 0),               # id -nG (нет sudo/wheel)
            _run_result("dbos:x:1001:", "", 0),         # getent внутри create_user попытка 1
            _run_result("", "group sudo not found", 6), # usermod -G sudo fail
            _run_result("dbos:x:1001:", "", 0),         # post-fail user_exists
            _run_result("dbos\n", "", 0),               # post-fail id -nG (всё ещё нет)
            _run_result("dbos:x:1001:", "", 0),         # getent внутри create_user попытка 2
            _run_result("", "", 0),                     # usermod -G wheel success
            _run_result("", "", 0),                     # sudoers
            _run_result("", "", 0),                     # authorized_keys
        ])
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=conn))

        async with SshClient("h", "boot", "pwd") as ssh:
            await ssh.bootstrap_management_user("dbos", _PUBKEY)

        # Два вызова usermod.
        mod_calls = [
            c for c in conn.run.await_args_list
            if "usermod" in (c.args[0] if c.args else "")
        ]
        assert len(mod_calls) == 2

    async def test_user_already_in_sudo_group_skips_useradd(self, monkeypatch):
        # Пользователь уже существует и состоит в sudo-группе (предыдущий
        # bootstrap упал между useradd и sudoers-шагом). На повторе пропускаем
        # useradd/usermod целиком — идём сразу к sudoers + authorized_keys.
        conn = _conn([
            _run_result("dbos:x:1001:", "", 0),    # user_exists pre-check
            _run_result("dbos sudo\n", "", 0),     # id -nG — sudo на месте
            _run_result("", "", 0),                # sudoers
            _run_result("", "", 0),                # authorized_keys
        ])
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=conn))

        async with SshClient("h", "boot", "pwd") as ssh:
            await ssh.bootstrap_management_user("dbos", _PUBKEY)

        # Никаких useradd/usermod не должно быть вызвано.
        modify_calls = [
            c for c in conn.run.await_args_list
            if "useradd" in (c.args[0] if c.args else "")
            or "usermod" in (c.args[0] if c.args else "")
        ]
        assert modify_calls == []

    async def test_user_appeared_in_wheel_after_partial_failure(self, monkeypatch):
        # Edge-case: первая попытка useradd -G sudo упала, но useradd успел
        # завести аккаунт и какой-то другой агент (puppet/ansible) подсадил
        # его в wheel параллельно. Post-fail id -nG показывает wheel —
        # bootstrap должен считать это успехом, не делать вторую попытку.
        conn = _conn([
            _run_result("", "", 2),                              # user_exists pre-check
            _run_result("", "", 2),                              # getent внутри create_user
            _run_result("", "group 'sudo' does not exist", 6),   # useradd sudo fail
            _run_result("dbos:x:1001:", "", 0),                  # post-fail user_exists (есть!)
            _run_result("dbos wheel\n", "", 0),                  # id -nG → wheel
            _run_result("", "", 0),                              # sudoers
            _run_result("", "", 0),                              # authorized_keys
        ])
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=conn))

        async with SshClient("h", "boot", "pwd") as ssh:
            await ssh.bootstrap_management_user("dbos", _PUBKEY)

        # Только одна попытка useradd (sudo).
        useradd_calls = [
            c for c in conn.run.await_args_list
            if "useradd" in (c.args[0] if c.args else "")
        ]
        assert len(useradd_calls) == 1
        assert "-G sudo" in useradd_calls[0].args[0]

    async def test_both_sudo_and_wheel_fail_raises_last_error(self, monkeypatch):
        # Ни sudo, ни wheel не работают — bootstrap должен поднять SshError
        # с последней ошибкой (wheel-failure).
        conn = _conn([
            _run_result("", "", 2),                              # user_exists pre-check
            _run_result("", "", 2),                              # getent внутри create_user попытка 1
            _run_result("", "group sudo not found", 6),          # useradd sudo fail
            _run_result("", "", 2),                              # post-fail user_exists (нет)
            _run_result("", "", 2),                              # getent внутри create_user попытка 2
            _run_result("", "group wheel not found", 6),         # useradd wheel fail
            _run_result("", "", 2),                              # post-fail user_exists (нет)
        ])
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=conn))

        async with SshClient("h", "boot", "pwd") as ssh:
            with pytest.raises(SshError) as exc_info:
                await ssh.bootstrap_management_user("dbos", _PUBKEY)
        # Ошибка должна быть SSH_USERADD_FAILED (последняя попытка — wheel).
        assert exc_info.value.error_code == "SSH_USERADD_FAILED"

    async def test_non_useradd_ssh_error_not_swallowed(self, monkeypatch):
        # SshError с кодом НЕ SSH_USERADD_FAILED/SSH_USERMOD_FAILED (например,
        # SSH_CONNECT_FAILED) не поглощается fallback-логикой — пробрасывается.
        import asyncssh as _asyncssh

        conn = MagicMock(spec=_asyncssh.SSHClientConnection)
        conn.close = MagicMock()
        conn.wait_closed = AsyncMock()

        call_count = {"n": 0}

        async def raise_on_run(cmd, **kw):
            call_count["n"] += 1
            if "getent" in cmd or "id -nG" in cmd:
                res = MagicMock()
                res.stdout = ""
                res.stderr = ""
                res.exit_status = 2
                return res
            # useradd → SSH_RUN_FAILED (не USERADD/USERMOD)
            raise SshError(
                error_code="SSH_RUN_FAILED",
                host="h",
                message="channel lost",
            )

        conn.run = AsyncMock(side_effect=raise_on_run)
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=conn))

        async with SshClient("h", "boot", "pwd") as ssh:
            with pytest.raises(SshError) as exc_info:
                await ssh.bootstrap_management_user("dbos", _PUBKEY)
        assert exc_info.value.error_code == "SSH_RUN_FAILED"
