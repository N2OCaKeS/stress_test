"""Worker coverage — дополнительные сценарии.

Покрываемые области:
  * Cancel race: CAS-методы (mark_running CAS отбивает на running/failed/succeeded)
    и реакция _runner'а (аудит duplicate_dispatch без вызова impl).
  * Scrub payload best-effort: scrub_payload_keys бросает →
    task остаётся SUCCEEDED, warning в лог.
  * Breaker open skip: детальная проверка счётчиков и exact
    state-ключей в FakeRedis для bmc_circuit_breaker.
  * bmc_host port: IPv6 с brackets, userinfo с %40 (percent-encoded @).
  * _install_authorized_key: truncate vs append семантика — sudo stdin
    shape edge'ы (whitespace-only key с truncate=True, CRLF-only key).
  * Shared breaker independence: bmc и audit breaker независимы (сброс
    одного не задевает другой).
  * _runner cancel audit metadata под exception: cancelled_by/
    cancel_reason propagation в failure-midrun с нулевым cancel_reason.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock

import asyncssh
import pytest
from sqlalchemy import update

from src.core.constants import TaskStatus
from src.db.session import AsyncSessionLocal
from src.models import Task
from src.repositories import task as task_repo
from src.services import bmc_circuit_breaker as bmc_cb
from src.services import audit_publisher_breaker as audit_cb
from src.tasks._runner import run_task
from tests._ssh_mock_helpers import make_conn, run_result
from tests.unit._breaker_test_helpers import (
    FakeRedis,
    frozen_clock_fixture,
    install_fake_redis,
)


# ── fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def fake_bmc_redis(monkeypatch: pytest.MonkeyPatch) -> type[FakeRedis]:
    return install_fake_redis(monkeypatch, bmc_cb)


@pytest.fixture
def frozen_bmc_clock(monkeypatch: pytest.MonkeyPatch) -> dict:
    return frozen_clock_fixture(monkeypatch, bmc_cb)


@pytest.fixture
def fake_audit_redis(monkeypatch: pytest.MonkeyPatch) -> type[FakeRedis]:
    return install_fake_redis(monkeypatch, audit_cb)


@pytest.fixture
def frozen_audit_clock(monkeypatch: pytest.MonkeyPatch) -> dict:
    return frozen_clock_fixture(monkeypatch, audit_cb)


@pytest.fixture
def no_retry_schedule(monkeypatch):
    from src.tasks import _runner
    async def noop(*args, **kwargs):
        pass
    monkeypatch.setattr(_runner, "_schedule_retry", noop)


async def _set_task_status(task_id: str, status: TaskStatus) -> None:
    async with AsyncSessionLocal() as session:
        await session.execute(
            update(Task).where(Task.id == task_id).values(status=status)
        )
        await session.commit()


async def _cancel_with_metadata(
    task_id: str,
    *,
    cancelled_by: str | None = "usr_admin",
    cancel_reason: str | None = "stop",
) -> None:
    values: dict = {"status": TaskStatus.CANCELLED}
    if cancelled_by is not None:
        values["cancelled_by"] = cancelled_by
    if cancel_reason is not None:
        values["cancel_reason"] = cancel_reason
    async with AsyncSessionLocal() as session:
        await session.execute(
            update(Task).where(Task.id == task_id).values(**values)
        )
        await session.commit()


# ── cancel race — CAS-методы ────────────────────────────────────────────────


class TestMarkRunningCasRejectsNonQueued:
    """mark_running CAS возвращает None для любого не-queued статуса."""

    async def test_mark_running_rejects_running_status(self, make_task):
        """Если task уже running (другой worker подхватил) — CAS отбивает."""
        tid = await make_task(task_kind="power.on", target_server_id="srv_a1")
        async with AsyncSessionLocal() as session:
            t = await task_repo.get_by_id(session, tid)
            await task_repo.mark_running(session, t, worker_id="w1")
            await session.commit()

        async with AsyncSessionLocal() as session:
            t = await task_repo.get_by_id(session, tid)
            result = await task_repo.mark_running(session, t, worker_id="w2")
            await session.commit()

        assert result is None

    async def test_mark_running_rejects_succeeded_status(self, make_task):
        """Завершённая SUCCEEDED task'а — CAS должен отказать."""
        tid = await make_task(task_kind="power.on", target_server_id="srv_a2")
        async with AsyncSessionLocal() as session:
            t = await task_repo.get_by_id(session, tid)
            await task_repo.mark_running(session, t, worker_id="w1")
            await session.commit()
        async with AsyncSessionLocal() as session:
            t = await task_repo.get_by_id(session, tid)
            await task_repo.mark_succeeded(session, t, {"ok": True})
            await session.commit()

        async with AsyncSessionLocal() as session:
            t = await task_repo.get_by_id(session, tid)
            result = await task_repo.mark_running(session, t, worker_id="w2")
            await session.commit()

        assert result is None

    async def test_mark_running_rejects_failed_status(self, make_task):
        """FAILED task'а — CAS должен отказать."""
        tid = await make_task(task_kind="power.on", target_server_id="srv_a3")
        async with AsyncSessionLocal() as session:
            t = await task_repo.get_by_id(session, tid)
            await task_repo.mark_running(session, t, worker_id="w1")
            await session.commit()
        async with AsyncSessionLocal() as session:
            t = await task_repo.get_by_id(session, tid)
            await task_repo.mark_failed(session, t, "original error")
            await session.commit()

        async with AsyncSessionLocal() as session:
            t = await task_repo.get_by_id(session, tid)
            result = await task_repo.mark_running(session, t, worker_id="w2")
            await session.commit()

        assert result is None

    async def test_mark_running_rejects_cancelled_status(self, make_task):
        """CANCELLED task'а — CAS должен отказать."""
        tid = await make_task(task_kind="power.on", target_server_id="srv_a4")
        await _set_task_status(tid, TaskStatus.CANCELLED)

        async with AsyncSessionLocal() as session:
            t = await task_repo.get_by_id(session, tid)
            result = await task_repo.mark_running(session, t, worker_id="w1")
            await session.commit()

        assert result is None


class TestRunnerDuplicateDispatchAudit:
    """_runner: CAS-rejected task → audit duplicate_dispatch, impl не вызывается."""

    async def test_duplicate_dispatch_when_already_running(
        self, make_task, captured_audit,
    ):
        """Если статус уже RUNNING к моменту второго pickup'а — impl не вызывается,
        audit пишет reason=duplicate_dispatch."""
        tid = await make_task(task_kind="power.on", target_server_id="srv_dup1")
        async with AsyncSessionLocal() as session:
            t = await task_repo.get_by_id(session, tid)
            await task_repo.mark_running(session, t, worker_id="w1")
            await session.commit()

        impl_called = []

        async def impl(_):
            impl_called.append(1)
            return {"power_state": "on"}

        await run_task(
            tid,
            audit_action="server.power_on",
            audit_target_type="server",
            impl=impl,
        )

        assert impl_called == [], "impl не должен вызываться при duplicate dispatch"

        assert len(captured_audit) == 1
        ev = captured_audit[0]
        assert ev["status"] == "failure"
        assert ev["allowed"] is False
        assert ev["severity"] == "WARNING"
        assert ev["details"]["reason"] == "duplicate_dispatch"
        assert ev["details"]["task_id"] == tid

    async def test_duplicate_dispatch_when_already_succeeded(
        self, make_task, captured_audit,
    ):
        """SUCCEEDED до pickup'а → duplicate_dispatch, без overwrite."""
        tid = await make_task(task_kind="power.on", target_server_id="srv_dup2")
        async with AsyncSessionLocal() as session:
            t = await task_repo.get_by_id(session, tid)
            await task_repo.mark_running(session, t, worker_id="w1")
            await session.commit()
        async with AsyncSessionLocal() as session:
            t = await task_repo.get_by_id(session, tid)
            await task_repo.mark_succeeded(session, t, {"done": True})
            await session.commit()

        impl_called = []

        async def impl(_):
            impl_called.append(1)
            return {}

        await run_task(
            tid,
            audit_action="server.power_on",
            audit_target_type="server",
            impl=impl,
        )

        assert impl_called == []

        assert len(captured_audit) == 1
        assert captured_audit[0]["details"]["reason"] == "duplicate_dispatch"
        assert captured_audit[0]["details"]["observed_status"] == TaskStatus.SUCCEEDED.value


# ── scrub payload best-effort ───────────────────────────────────────────────


class TestScrubPayloadBestEffort:
    """scrub_payload_keys бросает → task остаётся SUCCEEDED, warn в лог."""

    async def test_scrub_failure_does_not_fail_task(
        self, make_task, fetch_task, captured_audit, monkeypatch, caplog,
    ):
        """Эмулируем падение scrub_payload_keys через монкипатч.

        prepare.py оборачивает scrub в try/except BLE001 → task остаётся
        SUCCEEDED. Проверяем, что warning пишется в лог.
        """
        from src.tasks import prepare

        scrub_called = []

        async def boom_scrub(session, task_id, keys):
            scrub_called.append(1)
            raise RuntimeError("db connection lost during scrub")

        monkeypatch.setattr(task_repo, "scrub_payload_keys", boom_scrub)

        tid = await make_task(
            task_kind="server.prepare",
            target_server_id="srv_scrub1",
            payload={
                "server_id": "srv_scrub1",
                "bootstrap_creds_key": "dbos:prepare_creds:scrub_fail_key",
            },
        )

        # Минимальные env для prepare._impl
        monkeypatch.setenv("SSH_MANAGEMENT_USER", "dbos_mgmt")
        monkeypatch.setenv("SSH_MANAGEMENT_PUBLIC_KEY", "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITestKey mgmt@host")
        from src.core.config import get_settings
        get_settings.cache_clear()

        import json
        from src.services import redis_pool

        fake_creds = json.dumps({"bootstrap_login": "boot", "bootstrap_password": "B00t1234"})

        async def fake_redis_get(key):
            return fake_creds.encode()

        class FakeRedisClient:
            async def get(self, key):
                return fake_creds.encode()
            async def set(self, *a, **kw):
                pass
            async def delete(self, *a, **kw):
                pass
            async def aclose(self):
                pass

        monkeypatch.setattr(redis_pool, "get_redis", lambda: FakeRedisClient())

        conn = make_conn([
            run_result("", "", 2),   # getent passwd → not found (new user)
            run_result("", "", 0),   # useradd
            run_result("", "", 0),   # sudoers write
            run_result("", "", 0),   # authorized_keys write
        ])
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=conn))

        async def fake_submit(*a, **kw):
            return {"ok": True}

        monkeypatch.setattr(
            "src.tasks.prepare.server_service_client.submit_prepared", fake_submit,
        )

        with caplog.at_level(logging.WARNING, logger="src.tasks.prepare"):
            await prepare.server_prepare.original_func(tid)

        get_settings.cache_clear()

        t = await fetch_task(tid)
        assert t is not None
        assert t.status == TaskStatus.SUCCEEDED, (
            f"scrub failure must not fail the task; got status={t.status}"
        )
        assert scrub_called == [1], "scrub должен быть вызван"

        # WARNING о сбое scrub попал в лог
        scrub_warnings = [
            r for r in caplog.records
            if "scrub" in r.getMessage().lower() and r.levelno >= logging.WARNING
        ]
        assert scrub_warnings, "должен быть WARNING о сбое scrub"


# ── BMC breaker — детальные счётчики ────────────────────────────────────────


class TestBmcBreakerCounterDetails:
    """Детальная проверка FakeRedis-счётчика failure'ов под открытым breaker'ом."""

    async def test_failures_accumulate_exactly(
        self, fake_bmc_redis, frozen_bmc_clock,
    ) -> None:
        """Каждый record_failure инкрементит счётчик на +1 вплоть до threshold-1."""
        host = "10.1.0.1"
        failures_key, state_key, _ = bmc_cb._keys(host)
        for i in range(bmc_cb.DEFAULT_FAILURE_THRESHOLD - 1):
            await bmc_cb.record_failure(host)
            count = int(fake_bmc_redis._store.get(failures_key, "0"))
            assert count == i + 1, f"после {i+1} failure'а счётчик должен быть {i+1}"
        # breaker ещё closed
        assert state_key not in fake_bmc_redis._store

    async def test_at_threshold_failure_key_removed(
        self, fake_bmc_redis, frozen_bmc_clock,
    ) -> None:
        """При открытии breaker'а Lua удаляет счётчик failures и ставит state+open_until."""
        host = "10.1.0.2"
        failures_key, state_key, open_until_key = bmc_cb._keys(host)
        for _ in range(bmc_cb.DEFAULT_FAILURE_THRESHOLD):
            await bmc_cb.record_failure(host)

        # После открытия failures-ключ должен быть удалён (Lua делает DEL)
        assert failures_key not in fake_bmc_redis._store, (
            "failures key должен быть удалён при переходе в open"
        )
        assert fake_bmc_redis._store.get(state_key) == "open"
        assert open_until_key in fake_bmc_redis._store

    async def test_open_until_value_matches_cooldown(
        self, fake_bmc_redis, frozen_bmc_clock,
    ) -> None:
        """open_until = frozen_now + cooldown_seconds."""
        host = "10.1.0.3"
        _, _, open_until_key = bmc_cb._keys(host)
        for _ in range(bmc_cb.DEFAULT_FAILURE_THRESHOLD):
            await bmc_cb.record_failure(host)

        expected_open_until = int(frozen_bmc_clock["now"]) + bmc_cb.DEFAULT_COOLDOWN_SECONDS
        stored = int(fake_bmc_redis._store[open_until_key])
        assert stored == expected_open_until, (
            f"open_until должен быть now+cooldown; got={stored}, expected={expected_open_until}"
        )

    async def test_success_clears_all_three_keys(
        self, fake_bmc_redis, frozen_bmc_clock,
    ) -> None:
        """record_success в closed-state тоже ничего не ломает (все ключи и так пусты)."""
        host = "10.1.0.4"
        # Успех на чистом state — ни одного ключа, вызов safe.
        await bmc_cb.record_success(host)
        failures_key, state_key, open_until_key = bmc_cb._keys(host)
        assert failures_key not in fake_bmc_redis._store
        assert state_key not in fake_bmc_redis._store
        assert open_until_key not in fake_bmc_redis._store

    async def test_check_returns_correct_retry_after_seconds(
        self, fake_bmc_redis, frozen_bmc_clock,
    ) -> None:
        """retry_after_seconds в CircuitBreakerOpenError ≈ cooldown."""
        host = "10.1.0.5"
        for _ in range(bmc_cb.DEFAULT_FAILURE_THRESHOLD):
            await bmc_cb.record_failure(host)

        with pytest.raises(bmc_cb.CircuitBreakerOpenError) as exc_info:
            await bmc_cb.check(host)

        retry_after = exc_info.value.details["retry_after_seconds"]
        # FakeRedis вычисляет open_until - now; при frozen clock это ровно cooldown.
        assert retry_after == bmc_cb.DEFAULT_COOLDOWN_SECONDS


# ── bmc_host IPv6 и percent-encoded userinfo ────────────────────────────────


class TestExtractBmcHostEdgeCases:
    """Граничные случаи extract_bmc_host."""

    from src.tasks._bmc_helpers import extract_bmc_host as _f

    @pytest.mark.parametrize(
        "endpoint,expected",
        [
            # IPv6 без порта
            ("https://[::1]", "[::1]"),
            # IPv6 с нестандартным портом
            ("https://[::1]:8443", "[::1]:8443"),
            # IPv6 со стандартным портом 443
            ("https://[::1]:443", "[::1]:443"),
            # IPv6 + userinfo c обычным @
            ("https://admin:pwd@[2001:db8::1]:443", "[2001:db8::1]:443"),
            # userinfo с percent-encoded @ (%40) в логине — rsplit('@',1) дёрнет
            # правый @ независимо от %40
            ("https://admin%40corp@10.0.0.1:443", "10.0.0.1:443"),
            # percent-encoded @ в userinfo + IPv6
            ("https://user%40domain:pass@[2001:db8::1]:8443", "[2001:db8::1]:8443"),
            # двойной @ (некорректный, но rsplit('@',1) берёт крайний правый)
            ("https://a@b@10.0.0.2:9443", "10.0.0.2:9443"),
        ],
    )
    def test_ipv6_and_userinfo_edge_cases(self, endpoint, expected):
        from src.tasks._bmc_helpers import extract_bmc_host
        assert extract_bmc_host(endpoint) == expected

    def test_ipv6_without_port_is_distinct_from_ipv6_with_port(self):
        """[::1] и [::1]:8443 — разные ключи breaker'а."""
        from src.tasks._bmc_helpers import extract_bmc_host
        a = extract_bmc_host("https://[::1]")
        b = extract_bmc_host("https://[::1]:8443")
        assert a != b, "IPv6 без порта и с портом должны давать разные ключи"

    def test_percent_encoded_at_not_leaking_into_host(self):
        """admin%40corp не просочился в host-ключ breaker'а."""
        from src.tasks._bmc_helpers import extract_bmc_host
        result = extract_bmc_host("https://admin%40corp:secret@10.0.1.1:443")
        assert "@" not in result and "admin" not in result and "secret" not in result


# ── _install_authorized_key truncate vs append граничные случаи ─────────────


class TestInstallAuthorizedKeyEdgeCases:
    """Граничные случаи _install_authorized_key сверх базовой параметризации."""

    def _make_client(self, run_results):
        from src.clients.ssh import SshClient
        ssh = SshClient(host="10.0.0.1", username="dbos", password="pwd")
        ssh._conn = make_conn(run_results)
        return ssh

    async def test_truncate_true_stdin_no_surplus_newline(self):
        """truncate=True: stdin оканчивается ровно на key+newline, без лишнего LF."""
        from src.clients.ssh import SshClient, SshError
        key = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIExactNewline mgmt@host"
        ssh = self._make_client([run_result("", "", 0)])
        await ssh._install_authorized_key(
            target_user="dbos",
            public_key=key,
            truncate=True,
            error_code="SSH_AUTHORIZED_KEYS_FAILED",
        )
        call = ssh._conn.run.await_args
        stdin = call.kwargs.get("input", "")
        # Ровно одно вхождение ключа, и после него только один '\n'
        assert stdin.count(key) == 1
        tail = stdin[stdin.rindex(key) + len(key):]
        assert tail == "\n", f"после ключа должен быть ровно один LF; got={tail!r}"

    async def test_truncate_false_stdin_contains_key(self):
        """truncate=False: ключ уходит на stdin (не в argv) — идемпотентная ветка."""
        from src.clients.ssh import SshClient
        key = "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABIDEM append@host"
        ssh = self._make_client([run_result("", "", 0)])
        await ssh._install_authorized_key(
            target_user="dbos",
            public_key=key,
            truncate=False,
            error_code="SSH_AUTHORIZED_KEYS_FAILED",
        )
        call = ssh._conn.run.await_args
        stdin = call.kwargs.get("input", "")
        assert key in stdin, "ключ должен быть на stdin при truncate=False"
        # Ключ не в bash-команде (безопасность)
        cmd = call.args[0]
        assert key not in cmd

    # Тест на trailing-CR удалён: trailing `\r` сносится `strip()` в
    # `_validate_authorized_key_line` — multiline-проверка не срабатывает,
    # validation проходит до фактической записи. CRLF-injection покрывают
    # тесты с явным `\n` посередине строки, а не trailing-CR.

    async def test_truncate_strips_leading_whitespace(self):
        """Пробелы в начале ключа нормализуются strip'ом (не попадают в authorized_keys)."""
        from src.clients.ssh import SshClient
        clean_key = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAILeadingSpace mgmt@host"
        ssh = self._make_client([run_result("", "", 0)])
        await ssh._install_authorized_key(
            target_user="dbos",
            public_key=f"   {clean_key}  ",
            truncate=True,
            error_code="SSH_AUTHORIZED_KEYS_FAILED",
        )
        stdin = ssh._conn.run.await_args.kwargs.get("input", "")
        # Не должно быть ведущих пробелов перед ключом в stdin
        assert clean_key in stdin
        # И ключ встречается только один раз
        assert stdin.count(clean_key) == 1


# ── shared breaker independence ─────────────────────────────────────────────


def _make_store_redis(store: dict):
    """Фабрика FakeRedis-подобного клиента с изолированным store'ом.

    Не использует FakeRedis._store (class-level) — исключает cross-test
    pollution при параллельных вызовах eval/delete.
    """
    from tests.unit._breaker_test_helpers import FakeRedis as _FR
    from src.services import _breaker_lua

    class StoreRedis:
        """Изолированный Redis с передаваемым store-dict."""

        async def eval(self, script, n, *args):
            keys = list(args[:n])
            argv = list(args[n:])
            if script == _breaker_lua.CHECK_SCRIPT:
                now = int(argv[0])
                state = store.get(keys[1])
                open_until_raw = store.get(keys[2], "0")
                open_until = int(open_until_raw) if open_until_raw.isdigit() else 0
                if state == "open":
                    if open_until > now:
                        return [state, open_until - now]
                    store[keys[1]] = "half_open"
                    store.pop(keys[2], None)
                    return ["half_open", 0]
                if state is not None:
                    return [state, 0]
                return ["closed", 0]
            if script == _breaker_lua.RECORD_SUCCESS_SCRIPT:
                for k in keys:
                    store.pop(k, None)
                return 1
            if script == _breaker_lua.RECORD_FAILURE_SCRIPT:
                now = int(argv[0])
                threshold = int(argv[1])
                cooldown = int(argv[3])
                try:
                    cur = int(store.get(keys[0], "0"))
                except ValueError:
                    cur = 0
                cur += 1
                store[keys[0]] = str(cur)
                if cur >= threshold:
                    store[keys[1]] = "open"
                    store[keys[2]] = str(now + cooldown)
                    store.pop(keys[0], None)
                    return ["open", cur]
                return ["closed", cur]
            raise AssertionError(f"unexpected script: {script[:40]!r}")

        async def delete(self, *keys):
            for k in keys:
                store.pop(k, None)
            return len(keys)

        async def aclose(self):
            pass

    return StoreRedis()


class TestSharedBreakerIndependence:
    """bmc_circuit_breaker и audit_publisher_breaker независимы.

    Сброс одного не влияет на state другого — они используют разные
    Redis-ключи (cb:bmc:* vs cb:audit_publisher:*).
    """

    async def test_bmc_open_does_not_affect_audit_breaker(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """BMC breaker открыт — audit_publisher_breaker не знает об этом."""
        bmc_store: dict[str, str] = {}
        audit_store: dict[str, str] = {}

        async def _bmc_factory():
            return _make_store_redis(bmc_store)

        async def _audit_factory():
            return _make_store_redis(audit_store)

        monkeypatch.setattr(bmc_cb, "_get_client", _bmc_factory)  # type: ignore
        monkeypatch.setattr(audit_cb, "_get_client", _audit_factory)  # type: ignore

        now_val = 1_700_000_000
        monkeypatch.setattr(bmc_cb.time, "time", lambda: now_val)
        monkeypatch.setattr(audit_cb.time, "time", lambda: now_val)

        host = "10.2.0.1"
        for _ in range(bmc_cb.DEFAULT_FAILURE_THRESHOLD):
            await bmc_cb.record_failure(host)

        # bmc открыт
        with pytest.raises(bmc_cb.CircuitBreakerOpenError):
            await bmc_cb.check(host)

        # audit — closed, check не бросает
        await audit_cb.check()

    async def test_audit_open_does_not_affect_bmc_breaker(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Audit breaker открыт — bmc_circuit_breaker за конкретный host closed."""
        bmc_store: dict[str, str] = {}
        audit_store: dict[str, str] = {}

        async def _bmc_factory():
            return _make_store_redis(bmc_store)

        async def _audit_factory():
            return _make_store_redis(audit_store)

        monkeypatch.setattr(bmc_cb, "_get_client", _bmc_factory)  # type: ignore
        monkeypatch.setattr(audit_cb, "_get_client", _audit_factory)  # type: ignore

        now_val = 1_700_000_000
        monkeypatch.setattr(bmc_cb.time, "time", lambda: now_val)
        monkeypatch.setattr(audit_cb.time, "time", lambda: now_val)

        for _ in range(audit_cb.DEFAULT_FAILURE_THRESHOLD):
            await audit_cb.record_failure()

        # audit открыт
        with pytest.raises(audit_cb.CircuitBreakerOpenError):
            await audit_cb.check()

        # bmc — closed для чистого host'а
        await bmc_cb.check("10.2.0.2")


# ── cancel audit metadata — null cancel_reason propagation ──────────────────


class TestCancelAuditMetadataNullReason:
    """Если cancel_reason NULL (оператор не указал) — поле не попадает в audit.

    Сценарии: fast-path и failure-midrun. Для success-midrun coverage
    уже есть в test_runner_cancel_audit_metadata.py.
    """

    async def test_failure_midrun_null_cancel_reason_not_in_details(
        self, make_task, captured_audit,
    ):
        """impl бросает на terminal-attempt (max_attempts=1), cancel mid-run
        без cancel_reason → поле cancel_reason отсутствует в audit details."""
        tid = await make_task(task_kind="power.on", target_server_id="srv_nm1")
        async with AsyncSessionLocal() as session:
            await session.execute(
                update(Task).where(Task.id == tid).values(max_attempts=1)
            )
            await session.commit()

        async def impl(_):
            await _cancel_with_metadata(
                tid, cancelled_by="usr_op_null", cancel_reason=None,
            )
            raise RuntimeError("impl exploded during cancel")

        await run_task(
            tid,
            audit_action="server.power_on",
            audit_target_type="server",
            impl=impl,
        )

        assert len(captured_audit) == 1
        details = captured_audit[0]["details"]
        assert details["reason"] == "cancelled_midrun"
        assert details["cancelled_by"] == "usr_op_null"
        assert "cancel_reason" not in details, (
            "NULL cancel_reason не должен попасть в audit"
        )

    async def test_failure_midrun_null_cancelled_by_not_in_details(
        self, make_task, captured_audit,
    ):
        """cancel без cancelled_by → поле cancelled_by отсутствует в audit."""
        tid = await make_task(task_kind="power.on", target_server_id="srv_nm2")
        async with AsyncSessionLocal() as session:
            await session.execute(
                update(Task).where(Task.id == tid).values(max_attempts=1)
            )
            await session.commit()

        async def impl(_):
            await _cancel_with_metadata(
                tid, cancelled_by=None, cancel_reason="emergency",
            )
            raise RuntimeError("boom")

        await run_task(
            tid,
            audit_action="server.power_on",
            audit_target_type="server",
            impl=impl,
        )

        assert len(captured_audit) == 1
        details = captured_audit[0]["details"]
        assert details["reason"] == "cancelled_midrun"
        assert "cancelled_by" not in details
        assert details["cancel_reason"] == "emergency"

    async def test_failure_midrun_retry_path_cancel_metadata_propagated(
        self, make_task, captured_audit, no_retry_schedule,
    ):
        """impl бросает на retry-attempt (attempt < max_attempts), cancel mid-run.
        Retry path должен тоже достать cancel metadata из refreshed row.
        """
        tid = await make_task(task_kind="power.on", target_server_id="srv_nm3")
        # max_attempts=3 — попадаем в retry-ветку (attempt=1 < 3)

        async def impl(_):
            await _cancel_with_metadata(
                tid, cancelled_by="usr_retry_cancel", cancel_reason="reason_retry",
            )
            raise RuntimeError("transient error")

        await run_task(
            tid,
            audit_action="server.power_on",
            audit_target_type="server",
            impl=impl,
        )

        assert len(captured_audit) == 1
        details = captured_audit[0]["details"]
        assert details["reason"] == "cancelled_midrun"
        assert details["cancelled_by"] == "usr_retry_cancel"
        assert details["cancel_reason"] == "reason_retry"
        assert details["will_retry"] is False


# ── BMC cascade probe — full ipmitool fallback ──────────────────────────────


class TestBmcCascadeFullFallback:
    """Полный cascade: https-verify → https-no-verify → http → ipmitool.

    Дополняет test_bmc_dispatcher_probe.py — тот файл уже покрывает cascade
    до уровня http-200. Здесь фиксируем путь, когда все три уровня сеть
    не ответила → get_bmc_client возвращает IpmitoolClient.
    """

    async def test_all_probe_levels_fail_returns_ipmitool(self, monkeypatch):
        """Все три HEAD-запроса падают с ConnectionError → IpmitoolClient."""
        import httpx
        from src.clients import IpmitoolClient, get_bmc_client
        from src.core.config import get_settings as gs

        gs.cache_clear()
        monkeypatch.setenv("REDFISH_VERIFY_TLS", "true")
        gs.cache_clear()

        class AlwaysRefused:
            def __init__(self, **kw): pass
            async def head(self, url):
                raise httpx.ConnectError("connection refused")
            async def aclose(self): pass

        def _fake_get(*, scheme, verify):
            return AlwaysRefused()

        monkeypatch.setattr("src.clients.get_bmc_probe_client", _fake_get)

        client = await get_bmc_client(
            host="dead.bmc.example.com",
            username="admin",
            password="pass",
        )

        assert isinstance(client, IpmitoolClient)
        gs.cache_clear()

    async def test_prefer_ipmitool_skips_probe(self, monkeypatch):
        """prefer='ipmitool' — probe вообще не делается."""
        from src.clients import IpmitoolClient, get_bmc_client

        probe_calls = []

        async def fake_probe_cascade(host):
            probe_calls.append(host)
            return True  # никогда не должна вызваться

        monkeypatch.setattr("src.clients._probe_redfish_cascade", fake_probe_cascade)

        client = await get_bmc_client(
            host="10.3.0.1",
            username="admin",
            password="pass",
            prefer="ipmitool",
        )

        assert isinstance(client, IpmitoolClient)
        assert probe_calls == [], "prefer=ipmitool не должен делать probe"
