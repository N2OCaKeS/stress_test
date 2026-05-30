"""Bootstrap reraise для существующего юзера: оба usermod падают.

`test_bootstrap_wheel_fallback.py::test_both_sudo_and_wheel_fail_*`
ловит useradd-вариант (юзер ещё не существует). Здесь параллельная
ветка: пользователь УЖЕ есть, но не в sudo/wheel — bootstrap идёт
по `modify_user` через `create_user`-existing-path. Если ни sudo, ни
wheel-группы на дистрибутиве нет (экзотика — кастомный stripped image),
оба usermod дают rc!=0 → последний SshError должен пробросеться наверх
с error_code SSH_USERMOD_FAILED.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import asyncssh
import pytest

from src.clients.ssh import SshClient, SshError
from tests._ssh_mock_helpers import make_conn, run_result


_PUBKEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITestKey dbos"


class TestBootstrapDoubleUsermodFail:
    async def test_existing_user_both_groups_fail_raises_usermod(
        self, monkeypatch,
    ):
        # Юзер уже есть (pre-check user_exists rc=0), но не в sudo/wheel
        # (id -nG возвращает только основную группу).
        # Дальше:
        #   попытка 1: getent внутри create_user rc=0 → modify_user
        #     дёрнет usermod -G sudo → rc=6 «group does not exist»
        #     → post-fail user_exists rc=0, id -nG всё ещё без sudo/wheel
        #     → retry на wheel
        #   попытка 2: getent rc=0 → usermod -G wheel → rc=6
        #     → post-fail user_exists rc=0, id -nG без sudo/wheel
        #   loop end → raise last_exc (SSH_USERMOD_FAILED для wheel).
        conn = make_conn([
            run_result("dbos:x:1001:", "", 0),              # outer user_exists
            run_result("dbos\n", "", 0),                    # id -nG pre-check
            run_result("dbos:x:1001:", "", 0),              # getent в create_user, попытка 1
            run_result("", "group sudo not found", 6),      # usermod -G sudo fail
            run_result("dbos:x:1001:", "", 0),              # post-fail user_exists
            run_result("dbos\n", "", 0),                    # post-fail id -nG (всё ещё нет)
            run_result("dbos:x:1001:", "", 0),              # getent в create_user, попытка 2
            run_result("", "group wheel not found", 6),     # usermod -G wheel fail
            run_result("dbos:x:1001:", "", 0),              # post-fail user_exists
            run_result("dbos\n", "", 0),                    # post-fail id -nG
        ])
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=conn))

        async with SshClient("h", "boot", "pwd") as ssh:
            with pytest.raises(SshError) as exc_info:
                await ssh.bootstrap_management_user("dbos", _PUBKEY)

        # Последняя попытка — wheel; usermod (а не useradd, т.к. юзер был).
        assert exc_info.value.error_code == "SSH_USERMOD_FAILED"

        # Sudoers / authorized_keys не должны были дёрнуться — bootstrap
        # упал раньше.
        cmds = [c.args[0] for c in conn.run.await_args_list]
        assert not any("visudo" in c for c in cmds)
        assert not any("authorized_keys" in c for c in cmds)

    async def test_existing_user_partial_group_recovery_after_first_fail(
        self, monkeypatch,
    ):
        # Race-вариант: первая попытка usermod -G sudo упала, но между
        # этим вызовом и post-fail-check'ом параллельный процесс
        # (например, ansible) подсадил юзера в sudo. Post-fail id -nG
        # видит sudo → bootstrap считает работу за этого шага сделанной
        # и переходит дальше к sudoers + authorized_keys БЕЗ retry на
        # wheel. Покрывает ветку `break` внутри post-fail-check'а для
        # уже-существующего юзера.
        conn = make_conn([
            run_result("dbos:x:1001:", "", 0),              # outer user_exists
            run_result("dbos\n", "", 0),                    # id -nG pre-check (без sudo)
            run_result("dbos:x:1001:", "", 0),              # getent внутри create_user
            run_result("", "group sudo not found", 6),      # usermod -G sudo fail
            run_result("dbos:x:1001:", "", 0),              # post-fail user_exists
            run_result("dbos sudo\n", "", 0),               # post-fail id -nG → теперь sudo есть!
            run_result("", "", 0),                          # sudoers
            run_result("", "", 0),                          # authorized_keys
        ])
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=conn))

        async with SshClient("h", "boot", "pwd") as ssh:
            await ssh.bootstrap_management_user("dbos", _PUBKEY)

        # Только одна попытка usermod (sudo), wheel-retry не случился.
        mod_calls = [
            c for c in conn.run.await_args_list
            if "usermod" in (c.args[0] if c.args else "")
        ]
        assert len(mod_calls) == 1
        assert "-G sudo" in mod_calls[0].args[0]
