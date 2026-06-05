"""Unit-тесты `src/clients/ssh.py` — реальный SshClient на asyncssh.

Покрываем:

* lifecycle: connect, double-connect idempotent, close, async-cm;
* `run`: happy path, sudo wrapping, timeout, auth-fail mapping;
* `set_password`: chpasswd happy, chpasswd-fail, invalid login,
  stderr scrubbing;
* `get_inventory`: парсинг lscpu JSON, lsblk JSON, /etc/os-release,
  partial-failure tolerance;
* helpers: `_parse_os_release`, `_sanitize_cmd`,
  `_scrub_password_echo`;
* host-key не проверяется (`known_hosts=None` → connect без отказа);
* sanity: пароль не утекает в `SshError.__str__`.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import asyncssh
import pytest

from src.clients.ssh import (
    SshClient,
    SshError,
    _parse_os_release,
    _sanitize_cmd,
    _scrub_password_echo,
)
from tests._ssh_mock_helpers import run_result as _run_result


# ── helpers ─────────────────────────────────────────────────────────────────


def _make_fake_conn(*, run_results=None, run_raises=None):
    """Сконструировать MagicMock SSHClientConnection.

    `run_results` — список `MagicMock(stdout=..., stderr=..., exit_status=...)`
    либо одно значение; AsyncMock-side_effect отдаёт по очереди.

    `run_raises` — exception, который должен бросить `.run()` (вместо
    результата). Удобно для timeout/auth.
    """
    conn = MagicMock(spec=asyncssh.SSHClientConnection)
    conn.close = MagicMock()
    conn.wait_closed = AsyncMock()
    if run_raises is not None:
        conn.run = AsyncMock(side_effect=run_raises)
    elif run_results is not None:
        if isinstance(run_results, list):
            conn.run = AsyncMock(side_effect=run_results)
        else:
            conn.run = AsyncMock(return_value=run_results)
    else:
        conn.run = AsyncMock(return_value=_run_result("", "", 0))
    return conn


def _ssh_timeout(reason: str = "timed out") -> asyncssh.TimeoutError:
    """asyncssh.TimeoutError требует 9 позиционных args (наследник ProcessError);
    оборачиваем в helper, чтобы не тащить boilerplate в тесты."""
    return asyncssh.TimeoutError(
        None, None, None, None, None, None, b"", b"", reason,
    )


def _ssh_channel_open_error() -> asyncssh.ChannelOpenError:
    return asyncssh.ChannelOpenError(1, "no channel")


# ── lifecycle ───────────────────────────────────────────────────────────────


class TestSshClientLifecycle:
    async def test_connect_calls_asyncssh_with_credentials(self, monkeypatch):
        conn = _make_fake_conn()
        connect_mock = AsyncMock(return_value=conn)
        monkeypatch.setattr(asyncssh, "connect", connect_mock)

        client = SshClient(
            host="10.0.0.1",
            username="ops",
            password="s3cret",
            port=2222,
            timeout=15,
        )
        await client.connect()
        connect_mock.assert_awaited_once()
        kwargs = connect_mock.call_args.kwargs
        assert kwargs["host"] == "10.0.0.1"
        assert kwargs["port"] == 2222
        assert kwargs["username"] == "ops"
        assert kwargs["password"] == "s3cret"
        assert kwargs["connect_timeout"] == 15
        await client.close()

    async def test_double_connect_idempotent(self, monkeypatch):
        conn = _make_fake_conn()
        connect_mock = AsyncMock(return_value=conn)
        monkeypatch.setattr(asyncssh, "connect", connect_mock)

        client = SshClient("h", "u", "p")
        await client.connect()
        await client.connect()
        assert connect_mock.await_count == 1
        await client.close()

    async def test_async_context_manager(self, monkeypatch):
        conn = _make_fake_conn()
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=conn))

        async with SshClient("h", "u", "p") as client:
            assert client._conn is conn
        # после выхода — закрыто
        conn.close.assert_called()

    async def test_close_idempotent(self, monkeypatch):
        client = SshClient("h", "u", "p")
        await client.close()  # без connect не падает
        # connect → close → close = no-op
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=_make_fake_conn()))
        await client.connect()
        await client.close()
        await client.close()

    async def test_connect_auth_failed_raises_ssh_error(self, monkeypatch):
        monkeypatch.setattr(
            asyncssh,
            "connect",
            AsyncMock(side_effect=asyncssh.PermissionDenied(reason="bad password")),
        )
        with pytest.raises(SshError) as exc_info:
            await SshClient("h", "u", "p").connect()
        assert exc_info.value.error_code == "SSH_AUTH_FAILED"
        assert exc_info.value.host == "h"

    async def test_connect_timeout_raises_ssh_error(self, monkeypatch):
        monkeypatch.setattr(
            asyncssh, "connect", AsyncMock(side_effect=TimeoutError("connect timed out")),
        )
        with pytest.raises(SshError) as exc_info:
            await SshClient("h", "u", "p", timeout=1).connect()
        assert exc_info.value.error_code == "SSH_CONNECT_FAILED"

    async def test_connect_os_error_raises_ssh_error(self, monkeypatch):
        monkeypatch.setattr(
            asyncssh, "connect", AsyncMock(side_effect=OSError("connection refused")),
        )
        with pytest.raises(SshError) as exc_info:
            await SshClient("h", "u", "p").connect()
        assert exc_info.value.error_code == "SSH_CONNECT_FAILED"

    async def test_connect_generic_asyncssh_error_maps_to_connect_failed(self, monkeypatch):
        """Произвольный наследник `asyncssh.Error` (не PermissionDenied,
        не ConnectionLost) — host-key fail, key-import fail, encryption fail —
        должен мапиться в SSH_CONNECT_FAILED без утечки исходного текста.
        """
        monkeypatch.setattr(
            asyncssh,
            "connect",
            AsyncMock(side_effect=asyncssh.HostKeyNotVerifiable("fingerprint mismatch")),
        )
        with pytest.raises(SshError) as exc_info:
            await SshClient("h", "u", "p").connect()
        assert exc_info.value.error_code == "SSH_CONNECT_FAILED"
        # Исходный текст не должен утекать наружу.
        assert "fingerprint mismatch" not in str(exc_info.value)

    async def test_connect_channel_open_error_maps_to_connect_failed(self, monkeypatch):
        """ChannelOpenError на этапе connect (нестандартный, но возможный
        путь) — тоже asyncssh.Error → SSH_CONNECT_FAILED.
        """
        monkeypatch.setattr(
            asyncssh,
            "connect",
            AsyncMock(side_effect=_ssh_channel_open_error()),
        )
        with pytest.raises(SshError) as exc_info:
            await SshClient("h", "u", "p").connect()
        assert exc_info.value.error_code == "SSH_CONNECT_FAILED"

    async def test_no_known_hosts_connects_without_verification(self, monkeypatch):
        # Флот часто переустанавливается, host-key меняется → host-key не
        # проверяем. SshClient захардкожен на known_hosts=None (accept-any) —
        # параметр настройки убран.
        connect_mock = AsyncMock(return_value=_make_fake_conn())
        monkeypatch.setattr(asyncssh, "connect", connect_mock)
        await SshClient("h", "u", "p").connect()
        connect_mock.assert_awaited_once()
        assert connect_mock.await_args.kwargs["known_hosts"] is None

    async def test_no_strict_setting_on_config(self):
        # Настройка strict-host-key убрана из Settings — server-SSH exempt.
        from src.core.config import Settings
        assert "ssh_strict_host_key_checking" not in Settings.model_fields


# ── run() ───────────────────────────────────────────────────────────────────


class TestSshClientRun:
    async def test_run_happy_returns_rc_out_err(self, monkeypatch):
        conn = _make_fake_conn(run_results=_run_result("hello\n", "", 0))
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=conn))

        async with SshClient("h", "u", "p") as ssh:
            rc, out, err = await ssh.run("echo hello")
        assert rc == 0
        assert out == "hello\n"
        assert err == ""

    async def test_run_returns_nonzero_rc_without_raising(self, monkeypatch):
        conn = _make_fake_conn(run_results=_run_result("", "boom\n", 7))
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=conn))

        async with SshClient("h", "u", "p") as ssh:
            rc, out, err = await ssh.run("false")
        assert rc == 7
        assert err == "boom\n"

    async def test_run_without_connect_raises(self):
        client = SshClient("h", "u", "p")
        with pytest.raises(SshError) as exc_info:
            await client.run("uname -a")
        assert exc_info.value.error_code == "SSH_NOT_CONNECTED"

    async def test_run_sudo_wraps_command_and_supplies_password(self, monkeypatch):
        conn = _make_fake_conn(run_results=_run_result("ok", "", 0))
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=conn))

        async with SshClient("h", "u", "s3cret") as ssh:
            await ssh.run("whoami", sudo=True)

        call_args = conn.run.call_args
        cmd = call_args.args[0]
        stdin = call_args.kwargs["input"]
        assert cmd.startswith("sudo -S -p '' ")
        assert "whoami" in cmd
        assert stdin.startswith("s3cret\n")

    async def test_run_timeout_raises_ssh_timeout(self, monkeypatch):
        conn = _make_fake_conn(run_raises=_ssh_timeout("read timed out"))
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=conn))

        async with SshClient("h", "u", "p") as ssh:
            with pytest.raises(SshError) as exc_info:
                await ssh.run("sleep 100")
        assert exc_info.value.error_code == "SSH_TIMEOUT"

    async def test_run_asyncssh_error_maps_to_run_failed(self, monkeypatch):
        conn = _make_fake_conn(run_raises=_ssh_channel_open_error())
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=conn))

        async with SshClient("h", "u", "p") as ssh:
            with pytest.raises(SshError) as exc_info:
                await ssh.run("uname -a")
        assert exc_info.value.error_code == "SSH_RUN_FAILED"

    async def test_run_connection_lost_maps_to_run_failed(self, monkeypatch):
        """ConnectionLost во время exec — наследник `asyncssh.Error`, ловится
        общим except в `run()` → `SSH_RUN_FAILED`.

        Аналог реальной картины: TCP-канал отвалился посередине команды
        (sshd упал, link reset). Мы не хотим, чтобы caller получил
        `(-1, "", "")` или сырое asyncssh-исключение — нужен явный
        SshError для retry-логики task'и.
        """
        conn = _make_fake_conn(run_raises=asyncssh.ConnectionLost("peer dropped"))
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=conn))

        async with SshClient("h", "u", "p") as ssh:
            with pytest.raises(SshError) as exc_info:
                await ssh.run("ls /")
        assert exc_info.value.error_code == "SSH_RUN_FAILED"
        assert "peer dropped" not in str(exc_info.value), (
            "SshError должен скрывать исходный текст asyncssh-исключения"
        )

    async def test_run_disconnect_error_maps_to_run_failed(self, monkeypatch):
        """DisconnectError от удалённого sshd (graceful disconnect) тоже
        наследник `asyncssh.Error` → `SSH_RUN_FAILED`.

        Симметрично ConnectionLost'у — разница в том, что DisconnectError
        приходит от sshd с reason-кодом, а ConnectionLost — от TCP/asyncio.
        Маппинг одинаковый.
        """
        conn = _make_fake_conn(
            run_raises=asyncssh.DisconnectError(11, "by application")
        )
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=conn))

        async with SshClient("h", "u", "p") as ssh:
            with pytest.raises(SshError) as exc_info:
                await ssh.run("uptime")
        assert exc_info.value.error_code == "SSH_RUN_FAILED"


# ── set_password() ──────────────────────────────────────────────────────────


class TestSshClientSetPassword:
    async def test_chpasswd_happy_path(self, monkeypatch):
        conn = _make_fake_conn(run_results=_run_result("", "", 0))
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=conn))

        async with SshClient("h", "ops", "current") as ssh:
            await ssh.set_password("root", "NewP@ss123")

        # Проверяем, что chpasswd прокрутился через sudo, stdin = "current\nroot:NewP@ss123\n"
        cmd = conn.run.call_args.args[0]
        assert "chpasswd" in cmd
        assert cmd.startswith("sudo -S -p '' ")
        stdin = conn.run.call_args.kwargs["input"]
        assert "root:NewP@ss123\n" in stdin
        assert stdin.startswith("current\n")

    async def test_chpasswd_nonzero_raises_chpasswd_failed(self, monkeypatch):
        conn = _make_fake_conn(run_results=_run_result("", "incorrect password attempt", 1))
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=conn))

        async with SshClient("h", "ops", "wrong") as ssh:
            with pytest.raises(SshError) as exc_info:
                await ssh.set_password("root", "NewP@ss123")
        assert exc_info.value.error_code == "SSH_CHPASSWD_FAILED"
        assert exc_info.value.returncode == 1

    async def test_chpasswd_invalid_login_rejected(self, monkeypatch):
        conn = _make_fake_conn()
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=conn))

        async with SshClient("h", "ops", "p") as ssh:
            with pytest.raises(SshError) as exc_info:
                await ssh.set_password("ro;rm -rf /", "x")
        assert exc_info.value.error_code == "SSH_INVALID_LOGIN"
        # chpasswd НЕ должен был быть вызван
        conn.run.assert_not_called()

    async def test_chpasswd_scrubs_password_from_stderr(self, monkeypatch):
        # Симулируем буг где chpasswd зачем-то echo'нул payload в stderr.
        conn = _make_fake_conn(
            run_results=_run_result("", "error: root:LeakedSecret bad form\n", 1),
        )
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=conn))

        async with SshClient("h", "ops", "p") as ssh:
            with pytest.raises(SshError) as exc_info:
                await ssh.set_password("root", "LeakedSecret")
        assert "LeakedSecret" not in exc_info.value.stderr
        assert "<PASSWORD>" in exc_info.value.stderr

    async def test_set_password_does_not_put_password_in_cmd_sanitized(self, monkeypatch):
        conn = _make_fake_conn(run_results=_run_result("", "denied", 1))
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=conn))

        async with SshClient("h", "ops", "p") as ssh:
            with pytest.raises(SshError) as exc_info:
                await ssh.set_password("root", "TopSecret!42")
        assert "TopSecret!42" not in exc_info.value.cmd_sanitized
        assert "TopSecret!42" not in str(exc_info.value)


# ── get_inventory() ─────────────────────────────────────────────────────────


_LSCPU_JSON = """
{"lscpu": [
  {"field": "Architecture:", "data": "x86_64"},
  {"field": "CPU(s):", "data": "8"},
  {"field": "Model name:", "data": "Intel Xeon E5-2670"}
]}
"""

_LSBLK_JSON = """
{"blockdevices": [
  {"name": "sda", "size": "500G", "type": "disk", "model": "INTEL SSD", "serial": "BTHV1234"}
]}
"""

_OS_RELEASE = """\
NAME="Astra Linux SE"
VERSION="1.7 (Smolensk)"
ID=astra
VERSION_ID="1.7"
PRETTY_NAME="Astra Linux SE 1.7"
"""

_LSPCI = """\
00:00.0 "Host bridge" "Intel" "440FX" -r02
00:01.0 "ISA bridge" "Intel" "PIIX3"
"""


class TestSshClientGetInventory:
    async def test_collects_all_blocks(self, monkeypatch):
        # Очерёдность вызовов: hostname, uname -a, lscpu, lsblk, cat /etc/os-release, lspci -mm
        conn = _make_fake_conn(run_results=[
            _run_result("srv-test-01\n"),
            _run_result("Linux srv-test-01 5.15.0-91-generic ...\n"),
            _run_result(_LSCPU_JSON),
            _run_result(_LSBLK_JSON),
            _run_result(_OS_RELEASE),
            _run_result(_LSPCI),
        ])
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=conn))

        async with SshClient("h", "root", "p") as ssh:
            facts = await ssh.get_inventory()

        assert facts["hostname"]["stdout"] == "srv-test-01"
        assert "5.15.0" in facts["kernel"]["stdout"]
        assert facts["cpu"]["data"]["lscpu"][0]["field"] == "Architecture:"
        assert facts["disks"]["data"]["blockdevices"][0]["name"] == "sda"
        assert facts["os"]["NAME"] == "Astra Linux SE"
        assert facts["os"]["VERSION_ID"] == "1.7"
        assert any("Host bridge" in line for line in facts["pci"]["devices"])

    async def test_partial_failure_lscpu_returns_error_block(self, monkeypatch):
        # Все ОК кроме lscpu (rc=127, command not found).
        conn = _make_fake_conn(run_results=[
            _run_result("srv-1\n"),
            _run_result("Linux ...\n"),
            _run_result("", "lscpu: command not found", 127),
            _run_result(_LSBLK_JSON),
            _run_result(_OS_RELEASE),
            _run_result(_LSPCI),
        ])
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=conn))

        async with SshClient("h", "root", "p") as ssh:
            facts = await ssh.get_inventory()

        assert "error" in facts["cpu"]
        assert facts["cpu"]["returncode"] == 127
        # остальные блоки целы
        assert "data" in facts["disks"]
        assert facts["os"]["NAME"] == "Astra Linux SE"

    async def test_invalid_json_returns_error_with_raw_stdout(self, monkeypatch):
        conn = _make_fake_conn(run_results=[
            _run_result("h\n"),
            _run_result("Linux\n"),
            _run_result("not json at all"),
            _run_result(_LSBLK_JSON),
            _run_result(_OS_RELEASE),
            _run_result(_LSPCI),
        ])
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=conn))

        async with SshClient("h", "root", "p") as ssh:
            facts = await ssh.get_inventory()

        assert "error" in facts["cpu"]
        assert "raw_stdout" in facts["cpu"]
        assert facts["cpu"]["raw_stdout"].startswith("not json")

    async def test_ssh_error_in_one_command_does_not_break_others(self, monkeypatch):
        # `run` бросает SshError на первой команде (hostname), остальные ОК.
        run_results = [
            _ssh_timeout("hostname stuck"),  # _capture_text ловит как SshError
            _run_result("Linux\n"),
            _run_result(_LSCPU_JSON),
            _run_result(_LSBLK_JSON),
            _run_result(_OS_RELEASE),
            _run_result(_LSPCI),
        ]
        conn = _make_fake_conn(run_results=run_results)
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=conn))

        async with SshClient("h", "root", "p") as ssh:
            facts = await ssh.get_inventory()

        assert "error" in facts["hostname"]
        assert facts["hostname"]["error"].startswith("SSH_TIMEOUT")
        assert facts["kernel"]["stdout"] == "Linux"


# ── helpers ─────────────────────────────────────────────────────────────────


class TestParseOsRelease:
    def test_strips_double_quotes(self):
        out = _parse_os_release('NAME="Astra Linux"\nVERSION="1.7"\n')
        assert out["NAME"] == "Astra Linux"
        assert out["VERSION"] == "1.7"

    def test_handles_unquoted_values(self):
        out = _parse_os_release("ID=astra\nVERSION_ID=1.7\n")
        assert out["ID"] == "astra"
        assert out["VERSION_ID"] == "1.7"

    def test_skips_comments_and_empty_lines(self):
        out = _parse_os_release("# comment\n\nNAME=A\n")
        assert out == {"NAME": "A"}

    def test_empty_input_returns_empty(self):
        assert _parse_os_release("") == {}


class TestSanitizeCmd:
    def test_password_eq_form(self):
        assert "<PASSWORD>" in _sanitize_cmd("ipmitool --password=hunter2")
        assert "hunter2" not in _sanitize_cmd("ipmitool --password=hunter2")

    def test_dash_p_form(self):
        sanitized = _sanitize_cmd("ipmitool -U root -P hunter2 lan print 1")
        assert "hunter2" not in sanitized
        assert "<PASSWORD>" in sanitized

    def test_echo_user_pass_form(self):
        sanitized = _sanitize_cmd('echo "ops:secret123" | chpasswd')
        assert "secret123" not in sanitized
        assert "ops:<PASSWORD>" in sanitized


class TestScrubPasswordEcho:
    def test_replaces_password_substring(self):
        assert _scrub_password_echo("error: leaked SuperSecret here", "SuperSecret") == "error: leaked <PASSWORD> here"

    def test_empty_password_returns_input(self):
        assert _scrub_password_echo("anything", "") == "anything"

    def test_password_not_present_returns_input(self):
        assert _scrub_password_echo("no leak", "secret") == "no leak"

    def test_masks_login_prefix_with_colon(self):
        # chpasswd payload form: echo "user:pass" | chpasswd
        scrubbed = _scrub_password_echo(
            "chpasswd: line 1: ops:SuperSecret malformed",
            "SuperSecret",
            login="ops",
        )
        assert "SuperSecret" not in scrubbed
        assert "ops:" not in scrubbed
        assert "<LOGIN>:<PASSWORD>" in scrubbed

    def test_login_none_keeps_login_visible(self):
        # Старый контракт: login не передан — login в stderr не трогаем.
        scrubbed = _scrub_password_echo(
            "error for ops: bad", "secret", login=None,
        )
        assert "ops:" in scrubbed
