"""Unit-тесты `SshClient._install_authorized_key` (общий helper).

До рефакторинга `_write_authorized_key` и `bootstrap_management_user`
держали свои почти одинаковые `bash -c '... authorized_keys ...'`
блоки — ~80% совпадения. Третий call-site (ротация ключа управляющего)
форсировал вынос в общий метод.

Проверяем helper напрямую: truncate=True/False реально подставляет
разные write-команды в bash, ключ уходит на stdin, валидация
(empty/multiline/prefix) отбивается одинаково.
"""

from __future__ import annotations

import pytest

from src.clients.ssh import SshClient, SshError
from tests._ssh_mock_helpers import make_conn, run_result


_PUBKEY = (
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakeFingerprintForUnitTestKey "
    "comment@host"
)


def _make_client_with_conn(run_results):
    """SshClient с уже «подключённым» mock-connection.

    Минуем connect(), подсовываем `_conn` напрямую — `_install_*` не
    требует реального TCP'я.
    """
    ssh = SshClient(host="10.0.0.1", username="dbos", password="pwd")
    ssh._conn = make_conn(run_results)
    return ssh


class TestInstallAuthorizedKeyTruncateFlag:
    async def test_truncate_true_uses_overwrite_redirect(self):
        """`truncate=True` → bash-команда содержит `>` (overwrite),
        не `>>` (append), и БЕЗ `grep -qxF` (idempotent-проверки нет —
        сознательная перезапись).
        """
        ssh = _make_client_with_conn([run_result("", "", 0)])

        await ssh._install_authorized_key(
            target_user="dbos",
            public_key=_PUBKEY,
            truncate=True,
            error_code="SSH_AUTHORIZED_KEYS_FAILED",
        )

        # Один вызов conn.run; разбираем команду.
        call = ssh._conn.run.await_args
        assert call is not None
        cmd = call.args[0]
        # truncate path → одиночный `>`, без `grep -qxF`.
        assert "> \"$home/.ssh/authorized_keys\"" in cmd
        assert "grep -qxF" not in cmd
        # И ключ всё равно идёт на stdin, а не argv.
        assert _PUBKEY not in cmd
        # sudo -S -p '' получает пароль на stdin перед public_key.
        assert _PUBKEY in call.kwargs.get("input", "")

    async def test_truncate_false_uses_idempotent_append(self):
        """`truncate=False` → `grep -qxF` + `>>`, ключ не дублируется."""
        ssh = _make_client_with_conn([run_result("", "", 0)])

        await ssh._install_authorized_key(
            target_user="dbos",
            public_key=_PUBKEY,
            truncate=False,
            error_code="SSH_AUTHORIZED_KEYS_FAILED",
        )

        call = ssh._conn.run.await_args
        cmd = call.args[0]
        assert "grep -qxF" in cmd
        assert ">> \"$home/.ssh/authorized_keys\"" in cmd
        # Ключ — на stdin, не в argv.
        assert _PUBKEY not in cmd
        # sudo -S -p '' получает пароль на stdin перед public_key.
        assert _PUBKEY in call.kwargs.get("input", "")


class TestInstallAuthorizedKeyValidation:
    async def test_empty_key_rejected(self):
        ssh = _make_client_with_conn([])
        with pytest.raises(SshError) as exc:
            await ssh._install_authorized_key(
                target_user="dbos",
                public_key="   ",
                truncate=False,
                error_code="SSH_AUTHORIZED_KEYS_FAILED",
            )
        assert exc.value.error_code == "SSH_INVALID_ARG"
        # Не дёрнули SSH — пустой ключ отбит до bash.
        ssh._conn.run.assert_not_awaited()

    async def test_multiline_key_rejected(self):
        ssh = _make_client_with_conn([])
        with pytest.raises(SshError) as exc:
            await ssh._install_authorized_key(
                target_user="dbos",
                public_key="ssh-ed25519 AAA\nssh-ed25519 BBB",
                truncate=False,
                error_code="SSH_AUTHORIZED_KEYS_FAILED",
            )
        assert exc.value.error_code == "SSH_INVALID_ARG"
        ssh._conn.run.assert_not_awaited()

    async def test_multiline_key_with_crlf_and_trailing_newline_rejected(self):
        """Атака «спрятать вторую строку через CRLF» отбивается.

        Без явной проверки `\\r` злоумышленник мог бы оформить ключ как
        `ssh-ed25519 AAA\\r\\nssh-rsa BBB`: shell-формирование команды
        могло бы протащить вторую строку в `authorized_keys`. SSH-клиент
        проверяет оба символа — `\\n` и `\\r`. Без trailing-`\\n` тоже
        отбивается: один embedded `\\r` уже делает ключ multiline.
        """
        ssh = _make_client_with_conn([])
        with pytest.raises(SshError) as exc:
            await ssh._install_authorized_key(
                target_user="dbos",
                public_key=(
                    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakeKey\r\n"
                    "ssh-rsa AAAAB3NzaC1yc2EAAAADAQAB hidden\n"
                ),
                truncate=False,
                error_code="SSH_AUTHORIZED_KEYS_FAILED",
            )
        assert exc.value.error_code == "SSH_INVALID_ARG"
        ssh._conn.run.assert_not_awaited()

    async def test_unknown_prefix_rejected(self):
        ssh = _make_client_with_conn([])
        with pytest.raises(SshError) as exc:
            await ssh._install_authorized_key(
                target_user="dbos",
                public_key="garbage-key-without-known-prefix data",
                truncate=False,
                error_code="SSH_AUTHORIZED_KEYS_FAILED",
            )
        assert exc.value.error_code == "SSH_INVALID_ARG"
        ssh._conn.run.assert_not_awaited()

    async def test_nonzero_rc_raises_with_custom_error_code(self):
        """`error_code` параметр пробрасывается в SshError — bootstrap'у
        нужно `SSH_PREPARE_FAILED`, обычному пути — `SSH_AUTHORIZED_KEYS_FAILED`.
        """
        ssh = _make_client_with_conn([run_result("", "permission denied", 1)])
        with pytest.raises(SshError) as exc:
            await ssh._install_authorized_key(
                target_user="dbos",
                public_key=_PUBKEY,
                truncate=False,
                error_code="SSH_PREPARE_FAILED",
            )
        assert exc.value.error_code == "SSH_PREPARE_FAILED"
        assert exc.value.returncode == 1


class TestForbiddenHomeGuard:
    """Caller знает home заранее → отбиваем системные пути до отправки SSH.

    Защита от случая, когда сверху приехал login типа `nobody` /
    `daemon`: их home — `/var/empty` или `/dev`, и без guard'а ключ
    лёг бы в неожиданное место. Bash-guard на стороне remote-команды
    дублирует тот же набор путей.
    """

    @pytest.mark.parametrize(
        "home",
        [
            "/",
            "/dev",
            "/var/empty",
            "/usr/sbin/nologin",
            "/sbin/nologin",
            "/bin/false",
        ],
    )
    async def test_known_system_home_rejected(self, home):
        ssh = _make_client_with_conn([])
        with pytest.raises(SshError) as exc:
            await ssh._install_authorized_key(
                target_user="nobody",
                public_key=_PUBKEY,
                truncate=False,
                error_code="SSH_AUTHORIZED_KEYS_FAILED",
                target_home=home,
            )
        assert exc.value.error_code == "SSH_INVALID_HOME"
        # До SSH дело не дошло — guard сработал до bash-команды.
        ssh._conn.run.assert_not_awaited()

    async def test_legit_home_passes_python_guard(self):
        # `/home/svc` не в списке → Python-guard молчит, команда уезжает
        # на хост. Mock возвращает rc=0, чтобы не упасть на пути после.
        ssh = _make_client_with_conn([run_result("", "", 0)])
        await ssh._install_authorized_key(
            target_user="svc",
            public_key=_PUBKEY,
            truncate=False,
            error_code="SSH_AUTHORIZED_KEYS_FAILED",
            target_home="/home/svc",
        )
        ssh._conn.run.assert_awaited_once()

    async def test_no_target_home_keeps_legacy_path(self):
        # Без явного target_home guard не активируется — проверка home
        # остаётся на bash-стороне (`case` после getent passwd).
        ssh = _make_client_with_conn([run_result("", "", 0)])
        await ssh._install_authorized_key(
            target_user="dbos",
            public_key=_PUBKEY,
            truncate=False,
            error_code="SSH_AUTHORIZED_KEYS_FAILED",
        )
        ssh._conn.run.assert_awaited_once()


class TestForbiddenHomeBashGuard:
    """Bash-команда содержит case-список запрещённых home'ов.

    Bash-guard ловит остаточный случай: caller не знал home, передал
    только login, а getent passwd на удалённой стороне вернул системный
    путь. Проверяем структурно — в собранной команде должны быть все
    шаблоны из `_FORBIDDEN_HOMES`.
    """

    async def test_bash_case_lists_all_forbidden_homes(self):
        ssh = _make_client_with_conn([run_result("", "", 0)])
        await ssh._install_authorized_key(
            target_user="dbos",
            public_key=_PUBKEY,
            truncate=False,
            error_code="SSH_AUTHORIZED_KEYS_FAILED",
        )
        cmd = ssh._conn.run.await_args.args[0]
        # Все запрещённые home'ы — в case-шаблонах.
        from src.clients.ssh import _FORBIDDEN_HOMES
        for home in _FORBIDDEN_HOMES:
            assert f'"{home}"' in cmd, (
                f"bash-guard должен содержать паттерн для {home!r}, "
                f"иначе getent-результат проскочит без проверки"
            )
        # Пустой home (отсутствие user'а в passwd) — тоже отбивается.
        assert '""' in cmd
        # case/esac — структурно корректное.
        assert "case " in cmd and "esac" in cmd


class TestValidSshKeyPrefixesShared:
    """Whitelist префиксов вынесен в module-level и используется в обоих
    путях записи ключа: `_install_authorized_key` (server_account) и
    `bootstrap_management_user` (pre-check management user'а).
    """

    async def test_constant_covers_expected_algorithms(self):
        from src.clients import ssh as ssh_module

        prefixes = ssh_module._VALID_SSH_KEY_PREFIXES
        # Каждый prefix должен оканчиваться пробелом — startswith-матч
        # с обязательным разделителем.
        assert all(p.endswith(" ") for p in prefixes)
        # Базовые алгоритмы, без которых production не взлетит.
        assert "ssh-rsa " in prefixes
        assert "ssh-ed25519 " in prefixes
        # FIDO/U2F — поддерживаем оба варианта.
        assert "sk-ssh-ed25519@openssh.com " in prefixes
        assert "sk-ecdsa-sha2-nistp256@openssh.com " in prefixes

    async def test_bootstrap_rejects_unknown_prefix_via_same_whitelist(self):
        """Pre-check в `bootstrap_management_user` берёт ту же константу —
        неизвестный префикс отбивается до useradd/sudoers (без
        побочных эффектов на /etc).
        """
        ssh = _make_client_with_conn([])
        with pytest.raises(SshError) as exc:
            await ssh.bootstrap_management_user(
                management_user="dbos",
                public_key="garbage-key-without-known-prefix data",
            )
        assert exc.value.error_code == "SSH_INVALID_ARG"
        assert "unsupported algorithm prefix" in exc.value.message
        ssh._conn.run.assert_not_awaited()
