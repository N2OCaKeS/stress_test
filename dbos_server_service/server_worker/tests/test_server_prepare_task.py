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
    run_result as _run_result,
)


async def _force_terminal(tid: str) -> None:
    """max_attempts=1 → следующий exception в _impl сразу даёт FAILED."""
    async with AsyncSessionLocal() as session:
        await session.execute(
            update(Task).where(Task.id == tid).values(max_attempts=1)
        )
        await session.commit()


_PUBKEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIabc dbos"


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

        useradd_cmd = conn.run.await_args_list[2].args[0]
        assert "useradd" in useradd_cmd
        assert "dbos" in useradd_cmd
        assert "-G sudo" in useradd_cmd

        sudoers_cmd = conn.run.await_args_list[3].args[0]
        assert "/etc/sudoers.d/dbos-management" in sudoers_cmd
        assert "visudo -cf" in sudoers_cmd
        # NOPASSWD-правило едет на stdin, не в командную строку.
        sudoers_stdin = conn.run.await_args_list[3].kwargs["input"]
        assert "dbos ALL=(ALL) NOPASSWD: ALL" in sudoers_stdin

        keys_cmd = conn.run.await_args_list[4].args[0]
        assert "authorized_keys" in keys_cmd
        assert "grep -qxF" in keys_cmd

    async def test_sudoers_uses_management_user_name(self, monkeypatch):
        # Имя юзера не хардкодится — берётся из аргумента.
        conn = _conn(_bootstrap_seq())
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=conn))
        async with SshClient("h", "boot", "boot-pwd") as ssh:
            await ssh.bootstrap_management_user("ctl", _PUBKEY)
        sudoers_cmd = conn.run.await_args_list[3].args[0]
        assert "/etc/sudoers.d/ctl-management" in sudoers_cmd
        sudoers_stdin = conn.run.await_args_list[3].kwargs["input"]
        assert "ctl ALL=(ALL) NOPASSWD: ALL" in sudoers_stdin

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
        # outer getent (rc=2) → inner getent (rc=2) → useradd (rc=0) →
        # sudoers (rc=1, visudo отбил).
        conn = _conn([
            _run_result("", "", 2),
            _run_result("", "", 2),
            _run_result("", "", 0),
            _run_result("", "invalid sudoers", 1),
        ])
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=conn))
        async with SshClient("h", "boot", "boot-pwd") as ssh:
            with pytest.raises(SshError) as ei:
                await ssh.bootstrap_management_user("dbos", _PUBKEY)
        assert ei.value.error_code == "SSH_PREPARE_FAILED"

    async def test_authorized_keys_failure_raises(self, monkeypatch):
        # outer getent → inner getent → useradd → sudoers → bash authorized_keys (rc=1)
        conn = _conn([
            _run_result("", "", 2),
            _run_result("", "", 2),
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
        return creds

    async def fake_delete(creds_key):
        delete_calls.append(creds_key)

    monkeypatch.setattr(prepare, "_read_bootstrap_creds", fake_read)
    monkeypatch.setattr(prepare, "_delete_bootstrap_creds", fake_delete)
    return read_calls, delete_calls


def _set_mgmt_env(monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("SSH_MANAGEMENT_USER", "dbos")
    monkeypatch.setenv("SSH_MANAGEMENT_PUBLIC_KEY", _PUBKEY)
    get_settings.cache_clear()


class TestPrepareHandler:
    async def test_bootstrap_and_submit(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        _set_mgmt_env(monkeypatch)
        _, delete_calls = _mock_creds(
            monkeypatch,
            {"bootstrap_login": "bootadmin", "bootstrap_password": "Boot1234"},
        )

        tid = await make_task(
            task_kind="server.prepare", target_server_id="srv_prep1",
            payload={
                "server_id": "srv_prep1",
                "bootstrap_creds_key": "dbos:prepare_creds:pcd_x",
                "host": "10.0.0.7",
                "target_department_id": "dep_a",
            },
        )

        conn = _conn(_bootstrap_seq())
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=conn))

        submit_calls = []
        async def fake_submit(server_id, management_user, target_department_id=None):
            submit_calls.append((server_id, management_user, target_department_id))
            return {"ok": True, "is_managed": True, "prepared_at": "2026-05-27T00:00:00Z"}
        monkeypatch.setattr(
            "src.tasks.prepare.server_service_client.submit_prepared", fake_submit,
        )

        await prepare.server_prepare.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        assert t.result["management_user"] == "dbos"
        assert t.result["prepared"] is True
        assert submit_calls == [("srv_prep1", "dbos", "dep_a")]
        # Креды удалены из Redis после успеха.
        assert delete_calls == ["dbos:prepare_creds:pcd_x"]

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
        conn = _conn(_bootstrap_seq())
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
        conn = _conn(_bootstrap_seq())
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
        conn2 = _conn(_bootstrap_seq())
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

        # Имитируем что попытка 1 уже отработала SSH — ставим маркер вручную.
        await prepare._mark_bootstrap_succeeded(tid)

        # SSH connect не должен вызываться — маркер пропускает шаг.
        monkeypatch.setattr(
            asyncssh, "connect",
            AsyncMock(side_effect=AssertionError("ssh must not be called on retry after bootstrap")),
        )

        submit_calls = []
        async def fake_submit(server_id, management_user, target_department_id=None):
            submit_calls.append((server_id, management_user))
            return {"ok": True}
        monkeypatch.setattr(
            "src.tasks.prepare.server_service_client.submit_prepared", fake_submit,
        )

        await prepare.server_prepare.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        assert submit_calls == [("srv_prep_marker", "dbos")]
        # Маркер вычищен после успеха.
        assert await prepare._read_bootstrap_succeeded(tid) is False

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
        conn = _conn(_bootstrap_seq())
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

        await prepare._mark_bootstrap_succeeded(tid)
        # SSH connect не должен вызываться — маркер пропускает шаг.
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
        conn = _conn(_bootstrap_seq())
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
        conn = _conn(_bootstrap_seq())
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

    async def test_malformed_json_raises_creds_missing(self, monkeypatch):
        from src.services import redis_pool
        from src.tasks import prepare as prepare_mod

        class _Client:
            async def get(self, key):
                return b"{not valid json"

            async def aclose(self):
                pass

        monkeypatch.setattr(redis_pool, "get_redis", lambda: _Client())

        with pytest.raises(SshError) as ei:
            await prepare_mod._read_bootstrap_creds("dbos:prepare_creds:pcd_x")
        assert ei.value.error_code == "SSH_BOOTSTRAP_CREDS_MISSING"

    async def test_non_utf8_raises_creds_missing(self, monkeypatch):
        from src.services import redis_pool
        from src.tasks import prepare as prepare_mod

        class _Client:
            async def get(self, key):
                # Заведомо невалидный UTF-8 — json.loads(bytes) сам декодит.
                return b"\xff\xfe\xfd"

            async def aclose(self):
                pass

        monkeypatch.setattr(redis_pool, "get_redis", lambda: _Client())

        with pytest.raises(SshError) as ei:
            await prepare_mod._read_bootstrap_creds("dbos:prepare_creds:pcd_x")
        assert ei.value.error_code == "SSH_BOOTSTRAP_CREDS_MISSING"
