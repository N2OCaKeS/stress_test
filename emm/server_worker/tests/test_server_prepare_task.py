"""Тесты бутстрапа управления сервером (#14).

* `SshClient.bootstrap_management_user` — useradd управляющего юзера + sudo,
  NOPASSWD-sudoers, установка authorized_keys, идемпотентность (повтор не
  дублирует), отбой пустого/multiline-ключа;
* end-to-end handler `server.prepare`: read bootstrap-creds из Redis (по
  ссылке-ключу из payload) → SSH bootstrap → submit_prepared callback →
  delete creds; bootstrap-креды не в payload и не в audit; retry с живым TTL
  работает; истёкший ключ → SSH_BOOTSTRAP_CREDS_MISSING.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import asyncssh
import pytest
from sqlalchemy import update

from src.clients.ssh import SshClient, SshError
from src.core.config import get_settings
from src.core.constants import TaskStatus
from src.db.session import AsyncSessionLocal
from src.models import Task
from src.tasks import _runner, prepare
from tests._ssh_mock_helpers import (
    bootstrap_seq as _bootstrap_seq,
    bootstrap_seq_existing_sudo as _bootstrap_seq_existing_sudo,
    make_conn as _conn,
    prepare_seq as _prepare_seq,
    run_result as _run_result,
    sudo_probe_result as _sudo_probe,
)


async def _force_terminal(tid: str) -> None:
    """max_attempts=1 → следующий exception в _impl сразу даёт FAILED."""
    async with AsyncSessionLocal() as session:
        await session.execute(
            update(Task).where(Task.id == tid).values(max_attempts=1)
        )
        await session.commit()


_PUBKEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIabc dbos"

# Реальная Ed25519-пара для mgmt_install: prepare импортирует приватный ключ
# для анти-локаут-проверки (`asyncssh.import_private_key`), поэтому он должен
# быть валидным PEM, а не плейсхолдером.
_MGMT_PRIV_KEY = asyncssh.generate_private_key("ssh-ed25519").export_private_key().decode()
_MGMT_PUB_KEY = asyncssh.generate_private_key("ssh-ed25519").export_public_key().decode().strip()


def _mgmt_install(user: str = "dbos") -> dict:
    """Per-server материал, который server_service кладёт в prepare-stash."""
    return {
        "management_user": user,
        "public_key": _MGMT_PUB_KEY,
        "private_key": _MGMT_PRIV_KEY,
        "password": "MgmtPw-24chars-abcdEFGH1",
    }


# ── SshClient.bootstrap_management_user ──────────────────────────────────────


class TestBootstrapManagementUser:
    async def test_useradd_sudoers_and_authorized_keys(self, monkeypatch):
        # Юзера нет: outer getent (rc=2) → inner getent (rc=2) → useradd →
        # sudoers → authorized_keys. Pre-check id -nG не вызывается, потому
        # что outer user_exists уже вернул False.
        conn = _conn(_bootstrap_seq())
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=conn))
        async with SshClient("h", "boot", "boot-pwd") as ssh:
            await ssh.bootstrap_management_user("dbos", _PUBKEY)

        useradd_cmd = conn.run.await_args_list[3].args[0]
        assert "useradd" in useradd_cmd
        assert "dbos" in useradd_cmd
        assert "-G sudo" in useradd_cmd
        # Байт-в-байт: bootstrap заводит управляющего юзера с sudo-группой и
        # /bin/bash, без home_dir. sudo-группа единственная (has_sudo=False,
        # groups=["sudo"]).
        assert useradd_cmd == "sudo -S -p '' useradd -m -s /bin/bash -G sudo dbos"

        sudoers_cmd = conn.run.await_args_list[4].args[0]
        assert "/etc/sudoers.d/dbos-management" in sudoers_cmd
        assert "visudo -cf" in sudoers_cmd
        # Байт-в-байт: запись через mktemp → visudo -cf → атомарный mv, chmod 440.
        assert sudoers_cmd == (
            "sudo -S -p '' bash -c 'set -e; tmp=$(mktemp); cat > \"$tmp\"; "
            "chmod 440 \"$tmp\"; visudo -cf \"$tmp\"; "
            "mv \"$tmp\" /etc/sudoers.d/dbos-management; "
            "chmod 440 /etc/sudoers.d/dbos-management'"
        )
        # NOPASSWD-правило едет на stdin, не в командную строку.
        sudoers_stdin = conn.run.await_args_list[4].kwargs["input"]
        assert "dbos ALL=(ALL) NOPASSWD: ALL" in sudoers_stdin
        # Байт-в-байт: sudo-пароль первой строкой, затем ровно правило.
        assert sudoers_stdin == "boot-pwd\ndbos ALL=(ALL) NOPASSWD: ALL\n"

        keys_cmd = conn.run.await_args_list[5].args[0]
        assert "authorized_keys" in keys_cmd
        assert "grep -qxF" in keys_cmd

    async def test_sudoers_uses_management_user_name(self, monkeypatch):
        # Имя юзера не хардкодится — берётся из аргумента.
        conn = _conn(_bootstrap_seq())
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=conn))
        async with SshClient("h", "boot", "boot-pwd") as ssh:
            await ssh.bootstrap_management_user("ctl", _PUBKEY)
        sudoers_cmd = conn.run.await_args_list[4].args[0]
        assert "/etc/sudoers.d/ctl-management" in sudoers_cmd
        # Байт-в-байт: имя юзера подставляется и в путь, и в правило — оба места.
        assert sudoers_cmd == (
            "sudo -S -p '' bash -c 'set -e; tmp=$(mktemp); cat > \"$tmp\"; "
            "chmod 440 \"$tmp\"; visudo -cf \"$tmp\"; "
            "mv \"$tmp\" /etc/sudoers.d/ctl-management; "
            "chmod 440 /etc/sudoers.d/ctl-management'"
        )
        sudoers_stdin = conn.run.await_args_list[4].kwargs["input"]
        assert "ctl ALL=(ALL) NOPASSWD: ALL" in sudoers_stdin
        assert sudoers_stdin == "boot-pwd\nctl ALL=(ALL) NOPASSWD: ALL\n"

    async def test_idempotent_existing_user(self, monkeypatch):
        # Юзер существует и уже в sudo-группе: outer getent (rc=0) →
        # id -nG (вывод "dbos sudo") → useradd/usermod пропускаются →
        # sudoers → authorized_keys.
        conn = _conn(_bootstrap_seq_existing_sudo())
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=conn))
        async with SshClient("h", "boot", "boot-pwd") as ssh:
            # уже существующий пользователь — не падаем.
            await ssh.bootstrap_management_user("dbos", _PUBKEY)

    async def test_empty_public_key_rejected(self, monkeypatch):
        conn = _conn([])
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=conn))
        async with SshClient("h", "boot", "boot-pwd") as ssh:
            with pytest.raises(SshError) as ei:
                await ssh.bootstrap_management_user("dbos", "   ")
        assert ei.value.error_code == "SSH_INVALID_ARG"

    async def test_multiline_public_key_rejected(self, monkeypatch):
        conn = _conn([])
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=conn))
        async with SshClient("h", "boot", "boot-pwd") as ssh:
            with pytest.raises(SshError) as ei:
                await ssh.bootstrap_management_user("dbos", "key-a\nkey-b")
        assert ei.value.error_code == "SSH_INVALID_ARG"

    async def test_sudoers_failure_raises(self, monkeypatch):
        # outer getent (rc=2) → inner getent (rc=2) → sudo -n true → useradd
        # (rc=0) → sudoers (rc=1, visudo отбил).
        conn = _conn([
            _run_result("", "", 2),
            _run_result("", "", 2),
            _sudo_probe(),
            _run_result("", "", 0),
            _run_result("", "invalid sudoers", 1),
        ])
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=conn))
        async with SshClient("h", "boot", "boot-pwd") as ssh:
            with pytest.raises(SshError) as ei:
                await ssh.bootstrap_management_user("dbos", _PUBKEY)
        assert ei.value.error_code == "SSH_PREPARE_FAILED"

    async def test_authorized_keys_failure_raises(self, monkeypatch):
        # outer getent → inner getent → sudo -n true → useradd → sudoers →
        # bash authorized_keys (rc=1)
        conn = _conn([
            _run_result("", "", 2),
            _run_result("", "", 2),
            _sudo_probe(),
            _run_result("", "", 0),
            _run_result("", "", 0),
            _run_result("", "permission denied", 1),
        ])
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=conn))
        async with SshClient("h", "boot", "boot-pwd") as ssh:
            with pytest.raises(SshError) as ei:
                await ssh.bootstrap_management_user("dbos", _PUBKEY)
        assert ei.value.error_code == "SSH_PREPARE_FAILED"


# ── Handler: server.prepare ──────────────────────────────────────────────────


def _mock_creds(monkeypatch, creds: dict | None):
    """Подменить чтение/удаление bootstrap-кред из Redis на in-memory.

    `creds=None` имитирует истёкший / отсутствующий ключ → handler должен
    поднять SSH_BOOTSTRAP_CREDS_MISSING.
    """
    read_calls: list[str] = []
    delete_calls: list[str] = []

    async def fake_read(creds_key):
        read_calls.append(creds_key)
        if creds is None:
            raise SshError(
                error_code="SSH_BOOTSTRAP_CREDS_MISSING",
                host="",
                message="missing or expired",
            )
        # Per-server управляющий материал кладётся в тот же stash рядом с
        # bootstrap-кредами; добавляем его, если тест не задал свой.
        if "mgmt_install" not in creds:
            return {**creds, "mgmt_install": _mgmt_install()}
        return creds

    async def fake_delete(creds_key):
        delete_calls.append(creds_key)

    monkeypatch.setattr(prepare, "_read_bootstrap_creds", fake_read)
    monkeypatch.setattr(prepare, "_delete_bootstrap_creds", fake_delete)
    return read_calls, delete_calls


def _set_mgmt_env(monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("SSH_MANAGEMENT_USER", "dbos")
    # Хардинг отключаем: per-server prepare теперь ещё ставит пароль (chpasswd)
    # и проверяет вход новым ключом — без отключения reload sshd тесты ждали бы
    # дополнительных команд. Сам хардинг покрыт в test_ssh_client.
    monkeypatch.setenv("SSH_HARDEN_AFTER_PREPARE", "false")
    get_settings.cache_clear()


def _stub_node_exporter(monkeypatch) -> list[tuple]:
    """Заглушить шаг установки node_exporter в конце prepare.

    Шаг идёт отдельной управляющей key-сессией после bootstrap'а и provision'а;
    скриптованный conn из `_prepare_seq` его команд не содержит. Возвращает
    список вызовов `(server_id, management_user, has_private_key)`.
    """
    calls: list[tuple] = []

    class _Session:
        host = "stub"

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return None

    def fake_build_session(creds, server_id):
        calls.append((
            server_id,
            creds.get("management_user"),
            bool(creds.get("management_private_key")),
        ))
        return _Session()

    async def fake_install(runner, target):
        return {"target": target, "returncode": 0, "output_tail": ""}

    monkeypatch.setattr(prepare.ssh_client, "build_session", fake_build_session)
    monkeypatch.setattr(prepare, "install_node_exporter", fake_install)
    return calls


class TestPrepareHandler:
    async def test_bootstrap_and_submit(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        _set_mgmt_env(monkeypatch)
        _, delete_calls = _mock_creds(
            monkeypatch,
            {"bootstrap_login": "bootadmin", "bootstrap_password": "Boot1234"},
        )
        node_exporter_calls = _stub_node_exporter(monkeypatch)

        tid = await make_task(
            task_kind="server.prepare", target_server_id="srv_prep1",
            payload={
                "server_id": "srv_prep1",
                "bootstrap_creds_key": "dbos:prepare_creds:pcd_x",
                "host": "10.0.0.7",
                "target_department_id": "dep_a",
            },
        )

        conn = _conn(_prepare_seq())
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=conn))

        submit_calls = []
        async def fake_submit(server_id, management_user, target_department_id=None, *, management_mode=None):
            submit_calls.append((server_id, management_user, target_department_id, management_mode))
            return {"ok": True, "is_managed": True, "prepared_at": "2026-05-27T00:00:00Z"}
        monkeypatch.setattr(
            "src.tasks.prepare.server_service_client.submit_prepared", fake_submit,
        )

        await prepare.server_prepare.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        assert t.result["management_user"] == "dbos"
        assert t.result["prepared"] is True
        # Probe-команда печатает пустой ASTRA → не-Астра → other_os.
        assert submit_calls == [("srv_prep1", "dbos", "dep_a", "other_os")]
        assert t.result["management_mode"] == "other_os"
        # Креды удалены из Redis после успеха.
        assert delete_calls == ["dbos:prepare_creds:pcd_x"]
        # node_exporter ставится под управляющей key-сессией, маркер снят.
        assert node_exporter_calls == [("srv_prep1", "dbos", True)]
        assert t.result["node_exporter"] is True
        assert await prepare._read_node_exporter_installed(tid) is False

        get_settings.cache_clear()

    async def test_bootstrap_creds_not_in_payload(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        _set_mgmt_env(monkeypatch)
        _mock_creds(
            monkeypatch,
            {"bootstrap_login": "bootadmin", "bootstrap_password": "Boot1234"},
        )

        tid = await make_task(
            task_kind="server.prepare", target_server_id="srv_prep2",
            payload={
                "server_id": "srv_prep2",
                "bootstrap_creds_key": "dbos:prepare_creds:pcd_y",
            },
        )
        _stub_node_exporter(monkeypatch)
        conn = _conn(_prepare_seq())
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=conn))
        async def fake_submit(*a, **kw):
            return {"ok": True}
        monkeypatch.setattr(
            "src.tasks.prepare.server_service_client.submit_prepared", fake_submit,
        )

        await prepare.server_prepare.original_func(tid)

        t = await fetch_task(tid)
        # Plaintext-кред в payload нет — только ссылка на Redis-ключ, и та
        # после успешного prepare замаскирована scrub'ом (defense-in-depth:
        # ссылку в Redis-неймспейс не оставляем «висеть» в worker.tasks).
        assert "bootstrap_login" not in (t.payload or {})
        assert "bootstrap_password" not in (t.payload or {})
        assert t.payload["bootstrap_creds_key"] == "<scrubbed>"
        assert t.payload["server_id"] == "srv_prep2"

        get_settings.cache_clear()

    async def test_scrub_replaces_bootstrap_creds_key_on_success(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        """После успешного `submit_prepared` ключ `bootstrap_creds_key`
        в `tasks.payload` замаскирован на `<scrubbed>` — оператор с SELECT
        на worker.tasks не может попытаться прочитать секрет в Redis,
        даже если TTL ещё не истёк."""
        _set_mgmt_env(monkeypatch)
        _mock_creds(
            monkeypatch,
            {"bootstrap_login": "bootadmin", "bootstrap_password": "Boot1234"},
        )
        original_key = "dbos:prepare_creds:pcd_scrub"
        tid = await make_task(
            task_kind="server.prepare", target_server_id="srv_prep_scrub",
            payload={
                "server_id": "srv_prep_scrub",
                "bootstrap_creds_key": original_key,
            },
        )
        _stub_node_exporter(monkeypatch)
        conn = _conn(_prepare_seq())
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=conn))

        async def fake_submit(*a, **kw):
            return {"ok": True}
        monkeypatch.setattr(
            "src.tasks.prepare.server_service_client.submit_prepared", fake_submit,
        )

        await prepare.server_prepare.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        # Ключ замаскирован, но строка осталась в форенсике («здесь был ключ»).
        assert t.payload.get("bootstrap_creds_key") == "<scrubbed>"
        # Остальной payload остался нетронут.
        assert t.payload.get("server_id") == "srv_prep_scrub"
        # Plaintext-creds, разумеется, тоже не появились.
        assert "bootstrap_login" not in t.payload
        assert "bootstrap_password" not in t.payload

        get_settings.cache_clear()

    async def test_retry_with_live_ttl_finds_creds(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        # Креды читаются из Redis на каждой попытке — повтор находит их, пока
        # жив TTL (в отличие от старого scrub-перед-SSH, который ломал retry:
        # вторая попытка заходила под root без пароля). Первая попытка падает
        # транзиентно на connect (re-queue), вторая — успех.
        _set_mgmt_env(monkeypatch)
        read_calls, _ = _mock_creds(
            monkeypatch,
            {"bootstrap_login": "bootadmin", "bootstrap_password": "Boot1234"},
        )

        async def noop(*a, **kw):
            pass
        monkeypatch.setattr(_runner, "_schedule_retry", noop)

        tid = await make_task(
            task_kind="server.prepare", target_server_id="srv_prep_retry",
            payload={
                "server_id": "srv_prep_retry",
                "bootstrap_creds_key": "dbos:prepare_creds:pcd_r",
            },
        )

        async def fake_submit(*a, **kw):
            return {"ok": True}
        monkeypatch.setattr(
            "src.tasks.prepare.server_service_client.submit_prepared", fake_submit,
        )

        # Попытка 1: транзиентный сбой SSH-connect → mark_pending_for_retry.
        monkeypatch.setattr(
            asyncssh, "connect",
            AsyncMock(side_effect=asyncssh.ConnectionLost("reset")),
        )
        await prepare.server_prepare.original_func(tid)
        t = await fetch_task(tid)
        assert t.status == TaskStatus.QUEUED  # re-queued, не FAILED

        # Попытка 2: connect успешен → SUCCEEDED. Креды снова прочитаны из Redis.
        _stub_node_exporter(monkeypatch)
        conn2 = _conn(_prepare_seq())
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=conn2))
        await prepare.server_prepare.original_func(tid)
        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED

        assert read_calls == [
            "dbos:prepare_creds:pcd_r",
            "dbos:prepare_creds:pcd_r",
        ]
        get_settings.cache_clear()

    async def test_expired_ttl_fails_with_creds_missing(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        _set_mgmt_env(monkeypatch)
        _mock_creds(monkeypatch, None)  # ключа в Redis нет / истёк

        tid = await make_task(
            task_kind="server.prepare", target_server_id="srv_prep_exp",
            payload={
                "server_id": "srv_prep_exp",
                "bootstrap_creds_key": "dbos:prepare_creds:pcd_e",
            },
        )
        await _force_terminal(tid)

        async def noop(*a, **kw):
            pass
        monkeypatch.setattr(_runner, "_schedule_retry", noop)
        # SSH connect не должен даже вызваться — падаем на чтении кред.
        monkeypatch.setattr(
            asyncssh, "connect",
            AsyncMock(side_effect=AssertionError("connect must not be called")),
        )

        await prepare.server_prepare.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.FAILED
        assert "SSH_BOOTSTRAP_CREDS_MISSING" in (t.last_error or "")

        get_settings.cache_clear()

    async def test_retry_after_bootstrap_skips_ssh_when_creds_expired(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        """Race-fix: SSH-bootstrap прошёл, submit_prepared упал, ключ
        bootstrap-кред истёк по TTL → retry должен использовать маркер
        и пойти СРАЗУ в submit_prepared, не дёргая SSH.

        Без фикса: handler читал creds → SSH_BOOTSTRAP_CREDS_MISSING →
        FAILED, хотя сервер фактически готов. С фиксом: маркер «bootstrap
        отработал» в Redis сигналит, что SSH-этап можно пропустить.
        """
        _set_mgmt_env(monkeypatch)
        # На «попытке 2» кредов нет — TTL истёк.
        _mock_creds(monkeypatch, None)

        tid = await make_task(
            task_kind="server.prepare", target_server_id="srv_prep_marker",
            payload={
                "server_id": "srv_prep_marker",
                "bootstrap_creds_key": "dbos:prepare_creds:pcd_marker",
            },
        )

        async def noop(*a, **kw):
            pass
        monkeypatch.setattr(_runner, "_schedule_retry", noop)

        # Имитируем что попытка 1 уже отработала все SSH-шаги — ставим маркеры
        # вручную (bootstrap с детектнутым ранее режимом Смоленск + linked +
        # node_exporter). Все маркеры => retry идёт сразу в submit, не читая
        # stash (TTL истёк).
        await prepare._mark_bootstrap_succeeded(tid, "astra_smolensk")
        await prepare._mark_linked_provisioned(tid)
        await prepare._mark_node_exporter_installed(tid)

        # SSH connect не должен вызываться — маркеры пропускают шаги.
        monkeypatch.setattr(
            asyncssh, "connect",
            AsyncMock(side_effect=AssertionError("ssh must not be called on retry after bootstrap")),
        )

        submit_calls = []
        async def fake_submit(server_id, management_user, target_department_id=None, *, management_mode=None):
            submit_calls.append((server_id, management_user, management_mode))
            return {"ok": True}
        monkeypatch.setattr(
            "src.tasks.prepare.server_service_client.submit_prepared", fake_submit,
        )

        await prepare.server_prepare.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        # Режим из маркера донёсся в submit_prepared, несмотря на пропуск SSH.
        assert submit_calls == [("srv_prep_marker", "dbos", "astra_smolensk")]
        # Маркеры вычищены после успеха.
        assert await prepare._read_bootstrap_succeeded(tid) == (False, None)
        assert await prepare._read_node_exporter_installed(tid) is False

        get_settings.cache_clear()

    async def test_missing_creds_key_in_payload_fails(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        _set_mgmt_env(monkeypatch)
        _mock_creds(monkeypatch, {"bootstrap_login": "x", "bootstrap_password": "y"})

        tid = await make_task(
            task_kind="server.prepare", target_server_id="srv_prep_nokey",
            payload={"server_id": "srv_prep_nokey"},
        )
        await _force_terminal(tid)

        async def noop(*a, **kw):
            pass
        monkeypatch.setattr(_runner, "_schedule_retry", noop)
        monkeypatch.setattr(
            asyncssh, "connect",
            AsyncMock(side_effect=AssertionError("connect must not be called")),
        )

        await prepare.server_prepare.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.FAILED
        assert "SSH_BOOTSTRAP_CREDS_MISSING" in (t.last_error or "")

        get_settings.cache_clear()

    @pytest.mark.parametrize(
        "bad_key",
        [
            "",                                       # пустая
            "pcd_abc",                                # без префикса
            "dbos:prepare_creds:",                    # пустое тело
            "dbos:prepare_creds:*",                   # wildcard
            "dbos:prepare_creds:../etc/passwd",       # path traversal-подобное
            "dbos:prepare_creds:pcd_x;FLUSHALL",      # injection попытка
            "dbos:prepare_creds:pcd_" + "a" * 200,    # переполнение
            "other:keyspace:pcd_x",                   # чужой keyspace
        ],
    )
    async def test_malformed_creds_key_fails(
        self, make_task, fetch_task, monkeypatch, bad_key,
    ):
        # Любой ключ, не соответствующий формату server_service, должен
        # отбиваться до похода в Redis — иначе при компрометации очереди
        # атакующий мог бы заставить worker прочитать произвольное значение.
        _set_mgmt_env(monkeypatch)

        read_calls: list[str] = []

        async def fake_read(creds_key):
            read_calls.append(creds_key)
            return {"bootstrap_login": "x", "bootstrap_password": "y"}

        monkeypatch.setattr(prepare, "_read_bootstrap_creds", fake_read)
        monkeypatch.setattr(prepare, "_delete_bootstrap_creds", AsyncMock())

        payload: dict = {"server_id": "srv_prep_bad"}
        if bad_key:
            payload["bootstrap_creds_key"] = bad_key
        tid = await make_task(
            task_kind="server.prepare", target_server_id="srv_prep_bad",
            payload=payload,
        )
        await _force_terminal(tid)

        async def noop(*a, **kw):
            pass
        monkeypatch.setattr(_runner, "_schedule_retry", noop)
        monkeypatch.setattr(
            asyncssh, "connect",
            AsyncMock(side_effect=AssertionError("connect must not be called")),
        )

        await prepare.server_prepare.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.FAILED
        assert "SSH_BOOTSTRAP_CREDS_MISSING" in (t.last_error or "")
        # Чтения Redis не было — guard сработал до него.
        assert read_calls == []

        get_settings.cache_clear()

    async def test_well_formed_creds_key_passes_validation(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        # Sanity: валидный pcd_<hex> проходит guard и доходит до SSH-bootstrap.
        _set_mgmt_env(monkeypatch)
        _, delete_calls = _mock_creds(
            monkeypatch,
            {"bootstrap_login": "bootadmin", "bootstrap_password": "Boot1234"},
        )

        good_key = "dbos:prepare_creds:pcd_" + "a" * 32
        tid = await make_task(
            task_kind="server.prepare", target_server_id="srv_prep_ok",
            payload={"server_id": "srv_prep_ok", "bootstrap_creds_key": good_key},
        )
        _stub_node_exporter(monkeypatch)
        conn = _conn(_prepare_seq())
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=conn))
        async def fake_submit(*a, **kw):
            return {"ok": True}
        monkeypatch.setattr(
            "src.tasks.prepare.server_service_client.submit_prepared", fake_submit,
        )

        await prepare.server_prepare.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        assert delete_calls == [good_key]

        get_settings.cache_clear()

    async def test_marker_skips_ssh_and_logs_warning_on_malformed_key(
        self, make_task, fetch_task, captured_audit, monkeypatch, caplog,
    ):
        """Edge case: на retry'е через маркер SSH-этап пропускается, но
        bootstrap_creds_key в payload остался malformed. Cleanup в Redis НЕ
        должен дёргаться (защита от чужого keyspace), но нужно явное
        warning в логах, чтобы оператор увидел висящий до TTL ключ.
        """
        import logging

        _set_mgmt_env(monkeypatch)
        # creds в Redis не нужны — SSH пропускается через маркер.
        _mock_creds(monkeypatch, None)
        # Подмена _delete_bootstrap_creds — если cleanup задёргается, тест
        # это увидит.
        delete_calls: list[str] = []
        async def fake_delete(creds_key):
            delete_calls.append(creds_key)
        monkeypatch.setattr(prepare, "_delete_bootstrap_creds", fake_delete)

        tid = await make_task(
            task_kind="server.prepare", target_server_id="srv_prep_bad_cleanup",
            payload={
                "server_id": "srv_prep_bad_cleanup",
                # Малформатный ключ — fullmatch не сработает.
                "bootstrap_creds_key": "other:keyspace:pcd_evil",
            },
        )

        async def noop(*a, **kw):
            pass
        monkeypatch.setattr(_runner, "_schedule_retry", noop)

        await prepare._mark_bootstrap_succeeded(tid, "other_os")
        await prepare._mark_linked_provisioned(tid)
        await prepare._mark_node_exporter_installed(tid)
        # SSH connect не должен вызываться — маркеры пропускают шаги.
        monkeypatch.setattr(
            asyncssh, "connect",
            AsyncMock(side_effect=AssertionError("ssh must not be called")),
        )
        async def fake_submit(*a, **kw):
            return {"ok": True}
        monkeypatch.setattr(
            "src.tasks.prepare.server_service_client.submit_prepared", fake_submit,
        )

        with caplog.at_level(logging.WARNING, logger="src.tasks.prepare"):
            await prepare.server_prepare.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        # Cleanup пропущен — keyspace чужой.
        assert delete_calls == []
        # Warning записан в лог.
        warn_msgs = [
            r for r in caplog.records
            if r.levelno >= logging.WARNING
            and "bootstrap_creds_key cleanup skipped" in r.getMessage()
        ]
        assert warn_msgs, "expected a warning about skipped cleanup on malformed key"

        get_settings.cache_clear()

    async def test_cleanup_delete_failure_swallowed_on_happy_path(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        """Happy path с успешным `submit_prepared`: cleanup `_delete_bootstrap_creds`
        падает Redis-исключением — task всё равно SUCCEEDED. TTL подчистит
        ключ; cleanup-эксепшен задавлен на уровне самого `_delete_bootstrap_creds`,
        внешний caller его не видит.
        """
        _set_mgmt_env(monkeypatch)
        # creds читаются ОК — идём по `already_bootstrapped=False` пути.
        # `_mock_creds` подменяет и `_read_bootstrap_creds`, и
        # `_delete_bootstrap_creds`. Нам нужен реальный контракт «cleanup
        # бросает, но handler не падает», поэтому отдельным monkeypatch'ом
        # подменяем cleanup на функцию, которая ловит свой же exception
        # (повторяя контракт реального `_delete_bootstrap_creds`).
        _mock_creds(
            monkeypatch,
            {"bootstrap_login": "bootadmin", "bootstrap_password": "Boot1234"},
        )

        import logging

        delete_calls: list[str] = []

        async def cleanup_with_swallowed_error(creds_key: str) -> None:
            delete_calls.append(creds_key)
            try:
                raise RuntimeError("redis connection reset during cleanup")
            except Exception:
                logging.getLogger("src.tasks.prepare").debug(
                    "failed to delete bootstrap creds key", exc_info=True,
                )

        monkeypatch.setattr(prepare, "_delete_bootstrap_creds", cleanup_with_swallowed_error)

        tid = await make_task(
            task_kind="server.prepare", target_server_id="srv_prep_cleanup_fail",
            payload={
                "server_id": "srv_prep_cleanup_fail",
                "bootstrap_creds_key": "dbos:prepare_creds:pcd_cleanup_fail",
            },
        )
        _stub_node_exporter(monkeypatch)
        conn = _conn(_prepare_seq())
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=conn))

        async def fake_submit(*a, **kw):
            return {"ok": True}
        monkeypatch.setattr(
            "src.tasks.prepare.server_service_client.submit_prepared", fake_submit,
        )

        # Без exception-leak'а наружу. Сам факт того, что вызов прошёл — это
        # тоже проверка ветки.
        await prepare.server_prepare.original_func(tid)

        t = await fetch_task(tid)
        # Падение cleanup'а не должно ломать handler.
        assert t.status == TaskStatus.SUCCEEDED
        # Cleanup был вызван ровно один раз — на валидном ключе.
        assert delete_calls == ["dbos:prepare_creds:pcd_cleanup_fail"]

        get_settings.cache_clear()

    async def test_bootstrap_password_not_in_audit(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        _set_mgmt_env(monkeypatch)
        _mock_creds(
            monkeypatch,
            {"bootstrap_login": "bootadmin", "bootstrap_password": "Boot1234"},
        )

        tid = await make_task(
            task_kind="server.prepare", target_server_id="srv_prep3",
            payload={
                "server_id": "srv_prep3",
                "bootstrap_creds_key": "dbos:prepare_creds:pcd_z",
            },
        )
        _stub_node_exporter(monkeypatch)
        conn = _conn(_prepare_seq())
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=conn))
        async def fake_submit(*a, **kw):
            return {"ok": True}
        monkeypatch.setattr(
            "src.tasks.prepare.server_service_client.submit_prepared", fake_submit,
        )

        await prepare.server_prepare.original_func(tid)
        assert "Boot1234" not in str(captured_audit)
        assert "bootadmin" not in str(captured_audit)

        get_settings.cache_clear()


# ── _read_bootstrap_creds: устойчивость к битому payload'у ────────────────────


class TestReadBootstrapCredsMalformed:
    """Битый JSON / non-UTF8 в Redis-значении → SSH_BOOTSTRAP_CREDS_MISSING.

    Конвертим в ту же ошибку, что и пустой ключ — task FAILED с понятным
    last_error, а не traceback (json.JSONDecodeError / UnicodeDecodeError).
    """

    async def test_malformed_token_raises_stash_token_invalid(self, monkeypatch):
        """Corrupt-данные в Redis (не encrypted-token) → STASH_TOKEN_INVALID.

        После envelope-шифрования stash'а битый payload диагностируется как
        крипто-фейл (AppException), а не как «истёк TTL» (SshError). Это
        разделение умышленное: оператор должен видеть «крипто отказало»
        отдельно от штатного истечения окна.
        """
        from src.core.exceptions import AppException
        from src.services import redis_pool
        from src.tasks import prepare as prepare_mod

        class _Client:
            async def get(self, key):
                return b"{not valid json"

            async def aclose(self):
                pass

        monkeypatch.setattr(redis_pool, "get_redis", lambda: _Client())

        with pytest.raises(AppException) as ei:
            await prepare_mod._read_bootstrap_creds("dbos:prepare_creds:pcd_x")
        # `{not valid json` не начинается с `v` → STASH_TOKEN_INVALID.
        assert ei.value.error_code == "STASH_TOKEN_INVALID"

    async def test_non_utf8_raises_stash_token_invalid(self, monkeypatch):
        """Невалидный UTF-8 → STASH_TOKEN_INVALID (envelope-token бы начинался
        с ASCII `v<N>$`)."""
        from src.core.exceptions import AppException
        from src.services import redis_pool
        from src.tasks import prepare as prepare_mod

        class _Client:
            async def get(self, key):
                return b"\xff\xfe\xfd"

            async def aclose(self):
                pass

        monkeypatch.setattr(redis_pool, "get_redis", lambda: _Client())

        with pytest.raises((AppException, UnicodeDecodeError)) as ei:
            await prepare_mod._read_bootstrap_creds("dbos:prepare_creds:pcd_x")
        # Либо decode упадёт на raw.decode, либо STASH_TOKEN_INVALID
        # на первом символе (не `v`). Оба сигнала — корректные.
        if isinstance(ei.value, AppException):
            assert ei.value.error_code == "STASH_TOKEN_INVALID"


# ── Provisioning привязанных аккаунтов на этапе prepare ──────────────────────


class TestPrepareProvisionsLinkedAccounts:
    """После bootstrap'а prepare заводит все привязанные аккаунты под
    управляющей key-сессией, переиспользуя `ssh_client.provision_user`.
    Креды аккаунтов приходят в bootstrap-stash полем `linked_accounts`.
    """

    async def test_provisions_each_linked_account(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        _set_mgmt_env(monkeypatch)
        _mock_creds(
            monkeypatch,
            {
                "bootstrap_login": "bootadmin",
                "bootstrap_password": "Boot1234",
                "linked_accounts": [
                    {
                        "account_id": "acc_1", "login": "appuser",
                        "password": "AppPw123", "ssh_public_key": "ssh-ed25519 AAAA app",
                        "ssh_private_key": "PEM", "has_sudo": True,
                        "unix_groups": ["docker"], "shell": "/bin/bash",
                        "home_dir": "/home/appuser",
                    },
                    {
                        "account_id": "acc_2", "login": "dbuser",
                        "password": None, "ssh_public_key": "ssh-ed25519 AAAA db",
                        "ssh_private_key": "PEM2", "has_sudo": False,
                        "unix_groups": [], "shell": None, "home_dir": None,
                    },
                    # Битый аккаунт без login'а — должен быть пропущен.
                    {"account_id": "acc_bad", "login": None},
                ],
            },
        )

        provision_calls = []

        async def fake_provision(credentials, server_id, **kwargs):
            provision_calls.append((credentials, server_id, kwargs))
            return {"provisioned": True}

        monkeypatch.setattr(
            "src.tasks.prepare.ssh_client.provision_user", fake_provision,
        )

        tid = await make_task(
            task_kind="server.prepare", target_server_id="srv_prov",
            payload={
                "server_id": "srv_prov",
                "bootstrap_creds_key": "dbos:prepare_creds:pcd_prov",
                "host": "10.0.0.9",
                "ssh_port": 2222,
            },
        )
        _stub_node_exporter(monkeypatch)
        conn = _conn(_prepare_seq())
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=conn))

        async def fake_submit(*a, **kw):
            return {"ok": True}
        monkeypatch.setattr(
            "src.tasks.prepare.server_service_client.submit_prepared", fake_submit,
        )

        await prepare.server_prepare.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        # Два валидных аккаунта заведены, битый пропущен.
        assert len(provision_calls) == 2
        logins = {c[2]["login"] for c in provision_calls}
        assert logins == {"appuser", "dbuser"}
        for creds, sid, kwargs in provision_calls:
            assert sid == "srv_prov"
            # Управляющая key-сессия под management_user.
            assert creds["is_managed"] is True
            assert creds["management_user"] == "dbos"
            assert creds["host"] == "10.0.0.9"
            assert kwargs["force_replace"] is True
        # Пароль/ключ прокидываются корректно.
        by_login = {c[2]["login"]: c[2] for c in provision_calls}
        assert by_login["appuser"]["new_password"] == "AppPw123"
        assert by_login["appuser"]["has_sudo"] is True
        assert by_login["dbuser"]["new_password"] is None
        assert by_login["dbuser"]["public_key"] == "ssh-ed25519 AAAA db"

        get_settings.cache_clear()

    async def test_success_clears_stale_last_error(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        """Успешная attempt после упавшей чистит stale last_error.

        `mark_succeeded` не трогает last_error, поэтому без локальной очистки
        в prepare после фейла attempt 1 в last_error висела бы старая ошибка.
        """
        _set_mgmt_env(monkeypatch)
        _mock_creds(
            monkeypatch,
            {"bootstrap_login": "bootadmin", "bootstrap_password": "Boot1234"},
        )

        tid = await make_task(
            task_kind="server.prepare", target_server_id="srv_stale",
            payload={
                "server_id": "srv_stale",
                "bootstrap_creds_key": "dbos:prepare_creds:pcd_stale",
            },
        )
        # Симулируем остаток от упавшей предыдущей попытки.
        async with AsyncSessionLocal() as session:
            await session.execute(
                update(Task).where(Task.id == tid).values(
                    last_error="SSH_CHPASSWD_FAILED: prev attempt error",
                )
            )
            await session.commit()

        _stub_node_exporter(monkeypatch)
        conn = _conn(_prepare_seq())
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=conn))

        async def fake_submit(*a, **kw):
            return {"ok": True}
        monkeypatch.setattr(
            "src.tasks.prepare.server_service_client.submit_prepared", fake_submit,
        )

        await prepare.server_prepare.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        assert t.last_error is None

        get_settings.cache_clear()

    async def test_no_linked_accounts_is_noop(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        _set_mgmt_env(monkeypatch)
        _mock_creds(
            monkeypatch,
            {"bootstrap_login": "bootadmin", "bootstrap_password": "Boot1234"},
        )
        called = []

        async def fake_provision(*a, **kw):
            called.append(1)
            return {"provisioned": True}

        monkeypatch.setattr(
            "src.tasks.prepare.ssh_client.provision_user", fake_provision,
        )

        tid = await make_task(
            task_kind="server.prepare", target_server_id="srv_noacc",
            payload={
                "server_id": "srv_noacc",
                "bootstrap_creds_key": "dbos:prepare_creds:pcd_noacc",
            },
        )
        _stub_node_exporter(monkeypatch)
        conn = _conn(_prepare_seq())
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=conn))

        async def fake_submit(*a, **kw):
            return {"ok": True}
        monkeypatch.setattr(
            "src.tasks.prepare.server_service_client.submit_prepared", fake_submit,
        )

        await prepare.server_prepare.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        assert called == []

        get_settings.cache_clear()

    async def test_retry_provisions_linked_when_bootstrap_done_but_not_linked(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        """Ключевой фикс: bootstrap прошёл, provision привязанных учёток упал
        (sshd в окне reload'а после хардинга / бокс лёг) → retry НЕ должен
        пропускать provision по bootstrap-маркеру.

        Без фикса: bootstrap-маркер => весь блок (включая provision)
        пропускался, сервер репортился подготовленным, а ключи привязанных
        аккаунтов на бокс не доезжали. С фиксом: отдельный linked-маркер не
        выставлен => retry перечитывает stash и доводит provision до конца
        под управляющей key-сессией (повторного SSH-bootstrap по паролю нет —
        парольный вход после хардинга выключен).
        """
        _set_mgmt_env(monkeypatch)
        _mock_creds(
            monkeypatch,
            {
                "bootstrap_login": "bootadmin",
                "bootstrap_password": "Boot1234",
                "linked_accounts": [
                    {
                        "account_id": "acc_t", "login": "tester",
                        "password": "TesterPw1", "ssh_public_key": "ssh-ed25519 AAAA tester",
                        "ssh_private_key": "PEM", "has_sudo": False,
                        "unix_groups": [], "shell": None, "home_dir": "/home/tester",
                    },
                ],
            },
        )

        provision_calls = []

        async def fake_provision(credentials, server_id, **kwargs):
            provision_calls.append((credentials, server_id, kwargs))
            return {"provisioned": True}

        monkeypatch.setattr(
            "src.tasks.prepare.ssh_client.provision_user", fake_provision,
        )

        tid = await make_task(
            task_kind="server.prepare", target_server_id="srv_relink",
            payload={
                "server_id": "srv_relink",
                "bootstrap_creds_key": "dbos:prepare_creds:pcd_relink",
                "host": "10.0.0.9",
                "ssh_port": 2222,
            },
        )

        async def noop(*a, **kw):
            pass
        monkeypatch.setattr(_runner, "_schedule_retry", noop)

        # Имитируем «attempt 1 прошёл bootstrap, но не дошёл до linked-маркера».
        await prepare._mark_bootstrap_succeeded(tid, "other_os")

        # SSH-bootstrap по паролю не должен вызываться — bootstrap-маркер стоит,
        # provision привязанных учёток идёт через мок provision_user, а
        # node_exporter (маркера нет) доставляется заглушкой.
        monkeypatch.setattr(
            asyncssh, "connect",
            AsyncMock(side_effect=AssertionError("password bootstrap must not re-run")),
        )
        node_exporter_calls = _stub_node_exporter(monkeypatch)

        submit_calls = []
        async def fake_submit(server_id, management_user, target_department_id=None, *, management_mode=None):
            submit_calls.append((server_id, management_user, management_mode))
            return {"ok": True}
        monkeypatch.setattr(
            "src.tasks.prepare.server_service_client.submit_prepared", fake_submit,
        )

        await prepare.server_prepare.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        # Привязанная учётка заведена на retry'е, ключ доехал.
        assert len(provision_calls) == 1
        creds, sid, kwargs = provision_calls[0]
        assert kwargs["login"] == "tester"
        assert kwargs["public_key"] == "ssh-ed25519 AAAA tester"
        assert creds["is_managed"] is True
        assert creds["management_user"] == "dbos"
        # submit_prepared дошёл с режимом из bootstrap-маркера.
        assert submit_calls == [("srv_relink", "dbos", "other_os")]
        assert node_exporter_calls == [("srv_relink", "dbos", True)]
        # Оба маркера вычищены после успеха.
        assert await prepare._read_bootstrap_succeeded(tid) == (False, None)
        assert await prepare._read_linked_provisioned(tid) is False

        get_settings.cache_clear()

    async def test_linked_provision_failure_is_not_swallowed(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        """Фейл provision'а привязанной учётки НЕ должен молча давать success.

        Если `_provision_linked_accounts` упал — submit_prepared не зовётся,
        linked-маркер не ставится, task падает с реальной ошибкой. Bootstrap-
        маркер при этом уже стоит (SSH-bootstrap прошёл), чтобы retry не
        перезаходил по паролю после хардинга.
        """
        _set_mgmt_env(monkeypatch)
        _mock_creds(
            monkeypatch,
            {
                "bootstrap_login": "bootadmin",
                "bootstrap_password": "Boot1234",
                "linked_accounts": [
                    {
                        "account_id": "acc_t", "login": "tester",
                        "password": "TesterPw1", "ssh_public_key": "ssh-ed25519 AAAA tester",
                        "ssh_private_key": "PEM", "has_sudo": False,
                        "unix_groups": [], "shell": None, "home_dir": "/home/tester",
                    },
                ],
            },
        )

        async def boom_provision(*a, **kw):
            raise SshError(
                error_code="SSH_AUTHORIZED_KEYS_FAILED",
                host="10.0.0.9",
                message="key push failed",
            )

        monkeypatch.setattr(
            "src.tasks.prepare.ssh_client.provision_user", boom_provision,
        )

        tid = await make_task(
            task_kind="server.prepare", target_server_id="srv_linkfail",
            payload={
                "server_id": "srv_linkfail",
                "bootstrap_creds_key": "dbos:prepare_creds:pcd_linkfail",
                "host": "10.0.0.9",
            },
        )
        await _force_terminal(tid)

        async def noop(*a, **kw):
            pass
        monkeypatch.setattr(_runner, "_schedule_retry", noop)

        conn = _conn(_prepare_seq())
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=conn))

        submit_calls = []
        async def fake_submit(*a, **kw):
            submit_calls.append(a)
            return {"ok": True}
        monkeypatch.setattr(
            "src.tasks.prepare.server_service_client.submit_prepared", fake_submit,
        )

        await prepare.server_prepare.original_func(tid)

        t = await fetch_task(tid)
        # Прозрачный фейл: статус FAILED, реальная ошибка в last_error.
        assert t.status == TaskStatus.FAILED
        assert "SSH_AUTHORIZED_KEYS_FAILED" in (t.last_error or "")
        # submit_prepared не звался — сервер НЕ отрепортён подготовленным.
        assert submit_calls == []
        # linked-маркер не выставлен; bootstrap-маркер стоит (SSH прошёл).
        assert await prepare._read_linked_provisioned(tid) is False
        assert (await prepare._read_bootstrap_succeeded(tid))[0] is True

        get_settings.cache_clear()


# ── mgmt_install: ключ + пароль из prepare-stash ──────────────────────────────


class TestPrepareInstallsMgmtCreds:
    """prepare ставит per-server ключ и пароль из `mgmt_install`-stash'а."""

    async def test_installs_pubkey_and_sets_password(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        _set_mgmt_env(monkeypatch)
        _mock_creds(
            monkeypatch,
            {"bootstrap_login": "bootadmin", "bootstrap_password": "Boot1234"},
        )
        tid = await make_task(
            task_kind="server.prepare", target_server_id="srv_mgmt_creds",
            payload={
                "server_id": "srv_mgmt_creds",
                "bootstrap_creds_key": "dbos:prepare_creds:pcd_mgmt",
            },
        )
        _stub_node_exporter(monkeypatch)
        conn = _conn(_prepare_seq())
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=conn))

        async def fake_submit(*a, **kw):
            return {"ok": True}
        monkeypatch.setattr(
            "src.tasks.prepare.server_service_client.submit_prepared", fake_submit,
        )

        await prepare.server_prepare.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        # authorized_keys получил публичный ключ из mgmt_install (на stdin).
        keys_stdin = conn.run.await_args_list[6].kwargs["input"]
        assert _MGMT_PUB_KEY in keys_stdin
        # chpasswd выставил управляющий пароль из mgmt_install (login:pwd на stdin).
        chpasswd_cmd = conn.run.await_args_list[7].args[0]
        assert "chpasswd" in chpasswd_cmd
        chpasswd_stdin = conn.run.await_args_list[7].kwargs["input"]
        assert "dbos:MgmtPw-24chars-abcdEFGH1" in chpasswd_stdin

        get_settings.cache_clear()

    async def test_incomplete_mgmt_install_fails(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        _set_mgmt_env(monkeypatch)
        # mgmt_install присутствует, но без полей — fake_read его не дополняет.
        _mock_creds(
            monkeypatch,
            {
                "bootstrap_login": "bootadmin",
                "bootstrap_password": "Boot1234",
                "mgmt_install": {"management_user": "dbos"},
            },
        )
        tid = await make_task(
            task_kind="server.prepare", target_server_id="srv_mgmt_bad",
            payload={
                "server_id": "srv_mgmt_bad",
                "bootstrap_creds_key": "dbos:prepare_creds:pcd_badmgmt",
            },
        )
        await _force_terminal(tid)

        async def noop(*a, **kw):
            pass
        monkeypatch.setattr(_runner, "_schedule_retry", noop)
        monkeypatch.setattr(
            asyncssh, "connect",
            AsyncMock(side_effect=AssertionError("connect must not be called")),
        )

        await prepare.server_prepare.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.FAILED
        assert "SSH_MGMT_INSTALL_INCOMPLETE" in (t.last_error or "")

        get_settings.cache_clear()


# ── Детект редакции ОС: parse_management_mode (юнит на строках) ────────────────


class TestParseManagementMode:
    def test_not_astra_is_other_os(self):
        from src.clients.ssh import parse_management_mode
        assert parse_management_mode("ASTRA=\nLEVEL=\n") == "other_os"

    def test_empty_output_is_other_os(self):
        from src.clients.ssh import parse_management_mode
        assert parse_management_mode("") == "other_os"

    def test_astra_level_zero_is_orel(self):
        from src.clients.ssh import parse_management_mode
        assert parse_management_mode("ASTRA=1\nLEVEL=0\n") == "astra_orel"

    def test_astra_level_one_is_voronezh(self):
        from src.clients.ssh import parse_management_mode
        assert parse_management_mode("ASTRA=1\nLEVEL=1\n") == "astra_voronezh"

    def test_astra_level_two_is_smolensk(self):
        from src.clients.ssh import parse_management_mode
        assert parse_management_mode("ASTRA=1\nLEVEL=2\n") == "astra_smolensk"

    def test_astra_without_level_defaults_to_orel(self):
        # Astra-маркер есть, уровень не читается (нет modeswitch/mswitch.conf) →
        # консервативный дефолт базового режима, bootstrap не падает.
        from src.clients.ssh import parse_management_mode
        assert parse_management_mode("ASTRA=1\nLEVEL=\n") == "astra_orel"

    def test_level_with_noise_prefix(self):
        # modeswitch/mswitch.conf могут печатать «Current mode: 2» / «MODE=2» —
        # берём первый числовой символ из строки LEVEL.
        from src.clients.ssh import parse_management_mode
        assert parse_management_mode("ASTRA=1\nLEVEL=2\n") == "astra_smolensk"
        assert parse_management_mode("ASTRA=build-1.7\nLEVEL=1\n") == "astra_voronezh"

    def test_level_without_astra_marker_stays_other_os(self):
        # Уровень без Astra-маркера не делает ОС Астрой.
        from src.clients.ssh import parse_management_mode
        assert parse_management_mode("ASTRA=\nLEVEL=2\n") == "other_os"


# ── SshClient.detect_management_mode (через мок conn.run) ─────────────────────


class TestDetectManagementMode:
    async def test_detect_smolensk(self, monkeypatch):
        conn = _conn([_run_result("ASTRA=1\nLEVEL=2\n", "", 0)])
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=conn))
        async with SshClient("h", "boot", "boot-pwd") as ssh:
            assert await ssh.detect_management_mode() == "astra_smolensk"

    async def test_detect_other_os_on_probe_failure(self, monkeypatch):
        # Probe вернул non-zero (нет bash / экзотический образ) → other_os.
        conn = _conn([_run_result("", "boom", 1)])
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=conn))
        async with SshClient("h", "boot", "boot-pwd") as ssh:
            assert await ssh.detect_management_mode() == "other_os"


# ── bootstrap применяет пер-режимные groups + extra_create_commands ───────────


class TestBootstrapAppliesMode:
    async def test_extra_groups_in_useradd_and_commands_run(self, monkeypatch):
        # Юзера нет: getent(2) → inner getent(2) → sudo -n true → useradd →
        # sudoers → authorized_keys → две extra_create_commands.
        conn = _conn([
            _run_result("", "", 2),
            _run_result("", "", 2),
            _sudo_probe(),            # sudo -n true перед useradd
            _run_result("", "", 0),   # useradd
            _run_result("", "", 0),   # sudoers
            _run_result("", "", 0),   # authorized_keys
            _run_result("", "", 0),   # extra cmd 1
            _run_result("", "", 0),   # extra cmd 2
        ])
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=conn))
        async with SshClient("h", "boot", "boot-pwd") as ssh:
            await ssh.bootstrap_management_user(
                "dbos", _PUBKEY,
                groups=["astra-admin", "audit"],
                extra_create_commands=["pdpl-user -i 63 dbos", "touch /tmp/marker"],
            )
        useradd_cmd = conn.run.await_args_list[3].args[0]
        assert "-G sudo,astra-admin,audit" in useradd_cmd
        # extra-команды выполнены под sudo после authorized_keys.
        cmd5 = conn.run.await_args_list[6].args[0]
        cmd6 = conn.run.await_args_list[7].args[0]
        assert "sudo -S -p ''" in cmd5 and "pdpl-user -i 63 dbos" in cmd5
        assert "sudo -S -p ''" in cmd6 and "touch /tmp/marker" in cmd6

    async def test_failed_extra_command_raises(self, monkeypatch):
        conn = _conn([
            _run_result("", "", 2),
            _run_result("", "", 2),
            _sudo_probe(),            # sudo -n true перед useradd
            _run_result("", "", 0),   # useradd
            _run_result("", "", 0),   # sudoers
            _run_result("", "", 0),   # authorized_keys
            _run_result("", "command not found", 127),  # extra cmd падает
        ])
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=conn))
        async with SshClient("h", "boot", "boot-pwd") as ssh:
            with pytest.raises(SshError) as ei:
                await ssh.bootstrap_management_user(
                    "dbos", _PUBKEY,
                    extra_create_commands=["does-not-exist"],
                )
        assert ei.value.error_code == "SSH_PREPARE_FAILED"


# ── Handler: детект режима из payload-modes доходит до submit_prepared ─────────


class TestPrepareHandlerMode:
    async def test_astra_smolensk_mode_applied_and_submitted(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        _set_mgmt_env(monkeypatch)
        _mock_creds(
            monkeypatch,
            {"bootstrap_login": "bootadmin", "bootstrap_password": "Boot1234"},
        )

        tid = await make_task(
            task_kind="server.prepare", target_server_id="srv_prep_mode",
            payload={
                "server_id": "srv_prep_mode",
                "bootstrap_creds_key": "dbos:prepare_creds:pcd_mode",
                "host": "10.0.0.9",
                "target_department_id": "dep_a",
                "management_login": "dbos",
                "management_modes": {
                    "astra_orel": {"groups": [], "extra_create_commands": []},
                    "astra_voronezh": {"groups": [], "extra_create_commands": []},
                    "astra_smolensk": {
                        "groups": ["astra-admin"],
                        "extra_create_commands": ["pdpl-user -i 63 dbos"],
                    },
                    "other_os": {"groups": [], "extra_create_commands": []},
                },
            },
        )

        # Probe говорит Astra + уровень 2 (Смоленск), затем bootstrap-команды +
        # одна extra_create_command режима Смоленск.
        conn = _conn([
            _run_result("ASTRA=1\nLEVEL=2\n", "", 0),  # detect probe
            _run_result("", "", 2),                    # outer getent
            _run_result("", "", 2),                    # inner getent
            _sudo_probe(),                             # sudo -n true перед useradd
            _run_result("", "", 0),                    # useradd
            _run_result("", "", 0),                    # sudoers
            _run_result("", "", 0),                    # authorized_keys
            _run_result("", "", 0),                    # extra cmd Смоленска
            _run_result("", "", 0),                    # chpasswd mgmt-пароля
            _run_result("", "", 0),                    # verify `true` под ключом
        ])
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=conn))
        _stub_node_exporter(monkeypatch)

        submit_calls = []
        async def fake_submit(server_id, management_user, target_department_id=None, *, management_mode=None):
            submit_calls.append((server_id, management_user, target_department_id, management_mode))
            return {"ok": True}
        monkeypatch.setattr(
            "src.tasks.prepare.server_service_client.submit_prepared", fake_submit,
        )

        await prepare.server_prepare.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        assert t.result["management_mode"] == "astra_smolensk"
        assert submit_calls == [
            ("srv_prep_mode", "dbos", "dep_a", "astra_smolensk"),
        ]
        # Смоленская extra_create_command реально выполнена под sudo.
        extra_cmd = conn.run.await_args_list[7].args[0]
        assert "pdpl-user -i 63 dbos" in extra_cmd
        # useradd получил доп-группу режима.
        useradd_cmd = conn.run.await_args_list[4].args[0]
        assert "astra-admin" in useradd_cmd

        get_settings.cache_clear()
