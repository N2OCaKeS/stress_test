"""Coverage добор и регрессии для главного fix-цикла server_worker.

Покрываемые ветки (поведение НЕ меняем, только тесты):

* `_publish_one` breaker-skip при `retry_after == 0` теперь сдвигает
  `next_retry_at` на `_CB_SLEEP_CHUNK_SECONDS`. Без этого row крутилась
  бы в SELECT'е publisher'а на каждом poll-тике (open breaker → `check()`
  отбивает, commit noop, row снова eligible).
* `_on_publisher_exit` exc-branch: callback должен logger.critical при
  unhandled task exception (BaseException-подкласс проскочил try/except
  внутри loop'а).
* `internal_outbox_re_attempt`: guard'ы на non-int / bool row_id и
  except-branch при exception'е из `re_attempt_row`.
* `_schedule_retry`: exception из `task.kicker().kiq` (Redis-fault)
  идёт в WARNING с redact'ом, fire-and-forget не падает наружу.
* Sweep/cleanup-task'и DB-error branch: каждая `except Exception` ветка
  доходит до WARNING и return.
* `_warn_on_missing_audit_api_key`: WARNING при пустом
  `LOGGING_SERVICE_API_KEY`; noop с непустым.
* `_warmup_http_pools` exception-path: импорт http_pool бросает — хук
  пробрасывает (он же registered как startup-event, не глотает; здесь
  фиксируем поведение).
* `_drain_running_tasks` `pre_drain_status` теперь снимается до
  `mark_*` — regression от предыдущей версии.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select

from src.core.constants import TaskStatus
from src.db.session import AsyncSessionLocal
from src.models import AuditOutbox
from src.repositories import task as task_repo
from src.services import audit_outbox_publisher, audit_publisher_breaker
from tests._helpers.broker_mocks import make_broker

pytestmark = pytest.mark.asyncio


def _new_id(prefix: str = "tsk_") -> str:
    return f"{prefix}{uuid.uuid4().hex[:16]}"


# ── breaker-skip retry_after=0 теперь сдвигает next_retry_at ────────────────


class TestBreakerSkipRetryAfterZero:
    """`retry_after_seconds == 0` от open breaker'а теперь не оставляет
    row eligible на следующем тике — `next_retry_at` сдвигается на
    `_CB_SLEEP_CHUNK_SECONDS`.

    Регрессия: раньше при retry_after=0 публикатор делал noop — row
    оставалась unpublished без `next_retry_at` и SELECT следующего poll-
    цикла подбирал её снова, спин CPU-loop'ом по той же неработающей
    строке.
    """

    async def test_zero_retry_after_still_advances_next_retry(
        self, monkeypatch,
    ):
        """`CircuitBreakerOpenError(retry_after_seconds=0)` → next_retry_at
        сдвинут как минимум на _CB_SLEEP_CHUNK_SECONDS.
        """
        async with AsyncSessionLocal() as session:
            row = AuditOutbox(
                task_id="tsk_zero_retry",
                payload={"action": "server.power_on", "target_id": "srv_z"},
            )
            session.add(row)
            await session.commit()
            row_id = row.id

        # check() кидает с retry_after=0 (граничный случай: cooldown
        # истёк, breaker ещё в open, либо int()-округление от ~0.4s).
        async def boom_check():
            raise audit_publisher_breaker.CircuitBreakerOpenError(0)

        monkeypatch.setattr(
            audit_outbox_publisher.audit_publisher_breaker,
            "check",
            boom_check,
        )

        before = datetime.now(timezone.utc)
        published = await audit_outbox_publisher.flush_outbox()
        assert published == 0

        async with AsyncSessionLocal() as session:
            saved = await session.get(AuditOutbox, row_id)
        assert saved.next_retry_at is not None, (
            "next_retry_at должен быть сдвинут даже при retry_after=0"
        )
        delay = (saved.next_retry_at - before).total_seconds()
        chunk = audit_outbox_publisher._CB_SLEEP_CHUNK_SECONDS
        assert delay >= chunk - 1.0, (
            f"next_retry_at должен быть >= _CB_SLEEP_CHUNK_SECONDS; got={delay}"
        )
        # И не больше cap'а: row должна вернуться в выборку быстро,
        # как только breaker закроется.
        assert delay <= chunk + 1.0


# ── _on_publisher_exit exc-branch ──────────────────────────────────────────


class TestOnPublisherExitExceptionBranch:
    """`_on_publisher_exit` логирует CRITICAL при unhandled task exception."""

    async def test_cancelled_task_skipped(self, caplog):
        """task.cancelled() → callback тихо выходит, без логов."""
        from src.main import _on_publisher_exit

        task = MagicMock()
        task.cancelled.return_value = True
        task.exception.return_value = None
        task.get_name.return_value = "fake_loop"

        with caplog.at_level(logging.CRITICAL):
            _on_publisher_exit(task)

        assert not any(
            "background publisher loop exited" in r.message for r in caplog.records
        )

    async def test_unhandled_exception_logged_critical(self, caplog):
        """task.exception() возвращает RuntimeError → CRITICAL с redact'ом."""
        from src.main import _on_publisher_exit

        task = MagicMock()
        task.cancelled.return_value = False
        task.exception.return_value = RuntimeError("audit channel broke")
        task.get_name.return_value = "audit_outbox_publisher"

        with caplog.at_level(logging.CRITICAL):
            _on_publisher_exit(task)

        critical_records = [
            r for r in caplog.records if r.levelno == logging.CRITICAL
        ]
        assert critical_records, "должен быть CRITICAL-лог"
        msg = critical_records[0].getMessage()
        assert "audit_outbox_publisher" in msg
        assert "RuntimeError" in msg

    async def test_cancelled_error_from_exception_skipped(self, caplog):
        """task.exception() кидает CancelledError → callback выходит молча."""
        from src.main import _on_publisher_exit

        task = MagicMock()
        task.cancelled.return_value = False
        task.exception.side_effect = asyncio.CancelledError()

        with caplog.at_level(logging.CRITICAL):
            _on_publisher_exit(task)

        assert not any(
            "background publisher loop exited" in r.message for r in caplog.records
        )

    async def test_none_exception_no_log(self, caplog):
        """task.exception() = None (loop вернул штатно) → без логов."""
        from src.main import _on_publisher_exit

        task = MagicMock()
        task.cancelled.return_value = False
        task.exception.return_value = None

        with caplog.at_level(logging.CRITICAL):
            _on_publisher_exit(task)

        assert not any(
            "background publisher loop exited" in r.message for r in caplog.records
        )


# ── internal.outbox_re_attempt guard'ы ─────────────────────────────────────


class TestInternalOutboxReAttemptGuards:
    """Type-guard и except-branch для operator-ручки re-attempt."""

    async def test_non_int_row_id_rejected(self, caplog):
        from src.main import internal_outbox_re_attempt

        with caplog.at_level(logging.WARNING):
            ok = await internal_outbox_re_attempt("not-an-int")  # type: ignore[arg-type]

        assert ok is False
        assert any("row_id must be int" in r.message for r in caplog.records)

    async def test_bool_row_id_rejected(self, caplog):
        """`bool` — подкласс `int`, но 99% не то, что хотел оператор."""
        from src.main import internal_outbox_re_attempt

        with caplog.at_level(logging.WARNING):
            ok = await internal_outbox_re_attempt(True)  # type: ignore[arg-type]

        assert ok is False
        assert any("row_id must be int" in r.message for r in caplog.records)

    async def test_re_attempt_row_exception_logged_and_swallowed(
        self, monkeypatch, caplog,
    ):
        """`re_attempt_row` бросает DB-error → WARNING + return False."""
        from src.main import internal_outbox_re_attempt

        async def boom(_row_id):
            raise RuntimeError("DB connection refused")

        monkeypatch.setattr(
            "src.services.audit_outbox_publisher.re_attempt_row", boom,
        )

        with caplog.at_level(logging.WARNING):
            ok = await internal_outbox_re_attempt(42)

        assert ok is False
        warning_msgs = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
        assert any("internal.outbox_re_attempt" in m and "row=42" in m for m in warning_msgs)


# ── _schedule_retry exception-branch ───────────────────────────────────────


class TestScheduleRetryKickException:
    """`task.kicker().kiq` бросает на Redis-fault → WARNING с redact'ом."""

    async def test_kiq_exception_logged_and_swallowed(self, monkeypatch, caplog):
        """RuntimeError из kiq не должен сорвать background-task'у наружу."""
        from src.main import broker
        from src.tasks import _runner

        fake_broker = make_broker(kiq_exc=RuntimeError("Redis ECONNREFUSED"))
        monkeypatch.setattr(broker, "find_task", fake_broker.find_task)

        with caplog.at_level(logging.WARNING):
            # delay=0 — sleep моментально, не ждём back-off в тесте.
            await _runner._schedule_retry("power.on", "tsk_kiq_fail", 1, delay=0.0)
            # дать fire-and-forget tasks дотикать
            await asyncio.sleep(0.05)
            # дожимаем pending tasks
            for _ in range(5):
                pending = [t for t in _runner._RETRY_TASKS if not t.done()]
                if not pending:
                    break
                await asyncio.sleep(0.01)

        warning_msgs = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
        assert any("retry kick failed" in m and "tsk_kiq_fail" in m for m in warning_msgs), (
            f"warning лог о kick failure ожидается; got: {warning_msgs}"
        )


# ── Cleanup / sweep DB-error branches ──────────────────────────────────────


class TestSweepCleanupDbErrorBranches:
    """Каждая housekeeping-задача глотает Exception на DB-уровне и не валится."""

    async def test_tasks_sweep_orphaned_select_failure(
        self, monkeypatch, caplog,
    ):
        """list_orphaned_running кидает SQLAlchemyError → WARNING + return."""
        from src.main import tasks_sweep_orphaned

        async def boom(*a, **kw):
            raise RuntimeError("postgres connection terminated")

        monkeypatch.setattr(task_repo, "list_orphaned_running", boom)

        with caplog.at_level(logging.WARNING):
            await tasks_sweep_orphaned()

        warning_msgs = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
        assert any("tasks.sweep_orphaned: SELECT failed" in m for m in warning_msgs)

    async def test_tasks_cleanup_completed_old_db_failure(
        self, monkeypatch, caplog,
    ):
        from src.main import tasks_cleanup_completed_old

        async def boom(*a, **kw):
            raise RuntimeError("disk full")

        monkeypatch.setattr(task_repo, "delete_completed_older_than", boom)

        with caplog.at_level(logging.WARNING):
            await tasks_cleanup_completed_old()

        warning_msgs = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
        assert any("tasks.cleanup_completed_old failed" in m for m in warning_msgs)

    async def test_audit_outbox_cleanup_published_old_db_failure(
        self, monkeypatch, caplog,
    ):
        from src.main import audit_outbox_cleanup_published_old
        from src.repositories import audit_outbox as audit_outbox_repo

        async def boom(*a, **kw):
            raise RuntimeError("read-only transaction")

        monkeypatch.setattr(audit_outbox_repo, "delete_published_older_than", boom)

        with caplog.at_level(logging.WARNING):
            await audit_outbox_cleanup_published_old()

        warning_msgs = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
        assert any("audit_outbox.cleanup_published_old failed" in m for m in warning_msgs)

    async def test_worker_cleanup_stale_heartbeats_db_failure(
        self, monkeypatch, caplog,
    ):
        from src.main import worker_cleanup_stale_heartbeats
        from src.repositories import worker_heartbeat as heartbeat_repo

        async def boom(*a, **kw):
            raise RuntimeError("WAL stuck")

        monkeypatch.setattr(heartbeat_repo, "delete_stale_heartbeats", boom)

        with caplog.at_level(logging.WARNING):
            await worker_cleanup_stale_heartbeats()

        warning_msgs = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
        assert any("worker.cleanup_stale_heartbeats failed" in m for m in warning_msgs)


# ── _warn_on_missing_audit_api_key startup-hook ────────────────────────────


class TestWarnOnMissingAuditApiKey:
    """WARNING при пустом ключе; молчит если ключ есть."""

    async def test_empty_key_emits_warning(self, monkeypatch, caplog):
        import src.main as main_mod
        from taskiq import TaskiqState

        # Подменяем _settings локально, чтобы не дёргать get_settings.cache_clear.
        fake_settings = MagicMock()
        fake_settings.logging_service_api_key = ""
        fake_settings.app_env = "local"
        monkeypatch.setattr(main_mod, "_settings", fake_settings)

        with caplog.at_level(logging.WARNING):
            await main_mod._warn_on_missing_audit_api_key(TaskiqState())

        warning_msgs = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
        assert any(
            "LOGGING_SERVICE_API_KEY is empty" in m and "app_env=local" in m
            for m in warning_msgs
        )

    async def test_non_empty_key_silent(self, monkeypatch, caplog):
        import src.main as main_mod
        from taskiq import TaskiqState

        fake_settings = MagicMock()
        fake_settings.logging_service_api_key = "secret"
        fake_settings.app_env = "production"
        monkeypatch.setattr(main_mod, "_settings", fake_settings)

        with caplog.at_level(logging.WARNING):
            await main_mod._warn_on_missing_audit_api_key(TaskiqState())

        assert not any(
            "LOGGING_SERVICE_API_KEY is empty" in r.message for r in caplog.records
        )


# ── _warmup_http_pools exception-path ──────────────────────────────────────


class TestWarmupHttpPoolsExceptionPath:
    """Если один из get_*_client() бросает на старте — хук пробрасывает.

    Фиксируем поведение: startup-хуки taskiq должны падать наглядно, а не
    глотать ошибку конфигурации лимитов pool'а. Без теста изменение
    seman'тики (превратить в WARNING) прошло бы незаметно.
    """

    async def test_warmup_propagates_exception(self, monkeypatch):
        import src.main as main_mod
        from taskiq import TaskiqState

        def boom_get_audit_client(*a, **kw):
            raise ValueError("AUDIT_POOL_MAX_CONNECTIONS=0 invalid")

        monkeypatch.setattr(
            "src.services.http_pool.get_audit_client",
            boom_get_audit_client,
        )

        with pytest.raises(ValueError, match="AUDIT_POOL_MAX_CONNECTIONS"):
            await main_mod._warmup_http_pools(TaskiqState())

    async def test_warmup_happy_path_calls_all_factories(self, monkeypatch):
        """Happy-path: все четыре фабрики вызваны минимум по разу."""
        import src.main as main_mod
        from taskiq import TaskiqState

        calls = {
            "audit": 0, "server": 0, "probe": 0, "redfish": 0,
        }

        def make_counter(key, ret=None):
            def _fn(*a, **kw):
                calls[key] += 1
                return ret
            return _fn

        monkeypatch.setattr(
            "src.services.http_pool.get_audit_client",
            make_counter("audit"),
        )
        monkeypatch.setattr(
            "src.services.http_pool.get_server_service_client",
            make_counter("server"),
        )
        monkeypatch.setattr(
            "src.services.http_pool.get_bmc_probe_client",
            make_counter("probe"),
        )
        monkeypatch.setattr(
            "src.services.http_pool.get_bmc_redfish_transport",
            make_counter("redfish"),
        )

        await main_mod._warmup_http_pools(TaskiqState())

        assert calls["audit"] >= 1
        assert calls["server"] >= 1
        assert calls["probe"] >= 3  # три probe-комбинации
        assert calls["redfish"] >= 1


# ── _drain_running_tasks: pre_drain_status снимается ДО mark_* ─────────────


class TestDrainPreDrainStatusCapturedBefore:
    """Regression: pre_drain_status в audit-payload — статус ДО mark_*,
    а не после (RUNNING → QUEUED для retry / FAILED для terminal)."""

    async def test_pre_drain_status_is_running_not_post_mark(
        self, monkeypatch,
    ):
        """RUNNING task'а с retry-budget'ом: pre_drain_status='running',
        не 'queued' (хотя mark_pending_for_retry уже переписал status в DB).
        """
        import src.main as main_mod
        from src.tasks._runner_state import RUNNING_TASKS
        from taskiq import TaskiqState

        tid = _new_id()
        async with AsyncSessionLocal() as session:
            await task_repo.create(session, {
                "id": tid,
                "task_kind": "power.on",
                "target_server_id": "srv_drain",
                "payload": {"server_id": "srv_drain"},
                "status": TaskStatus.RUNNING,
                "attempt": 1,
                "max_attempts": 3,
                "worker_id": "test-worker",
                "started_at": datetime.now(timezone.utc),
                "created_by": "usr_test",
                "request_id": "req_test",
            })
            await session.commit()

        RUNNING_TASKS.add(tid)

        # Сильно ужимаем drain timeout: тест не ждёт.
        fake_settings = MagicMock(wraps=main_mod._settings)
        fake_settings.worker_shutdown_timeout_seconds = 0.01
        monkeypatch.setattr(main_mod, "_settings", fake_settings)

        try:
            await main_mod._drain_running_tasks(TaskiqState())
        finally:
            RUNNING_TASKS.discard(tid)

        # Достаём audit-row, проверяем pre_drain_status.
        async with AsyncSessionLocal() as session:
            rows = (await session.execute(
                select(AuditOutbox).where(AuditOutbox.task_id == tid)
            )).scalars().all()

        assert rows, "audit row для drain'нутой task'и должен быть записан"
        drain_audit = [
            r for r in rows
            if r.payload.get("action") == "task.worker_shutdown"
        ]
        assert drain_audit, "должен быть task.worker_shutdown audit"
        details = drain_audit[0].payload.get("details", {})
        assert details.get("pre_drain_status") == TaskStatus.RUNNING, (
            f"pre_drain_status должен быть 'running' (статус ДО mark_*), "
            f"got={details.get('pre_drain_status')}; full details={details}"
        )


# ── _filter_result_for_audit sentinel-ы (фикс закрыл раньше) ────────────────


class TestFilterResultForAuditSentinels:
    """Покрываем sentinel'ы `no_whitelist` и `result_not_dict` ещё раз —
    тесты в test_ipmi_stash_and_redaction_coverage.py их уже трогают, но
    локально удобнее держать вместе с остальными coverage-кейсами.
    """

    def test_no_whitelist_with_dict_result(self):
        from src.tasks._runner import _filter_result_for_audit

        out = _filter_result_for_audit({"power_state": "on"}, None)
        assert out == {
            "emitted": False,
            "reason": "no_whitelist",
            "result_type": "dict",
        }

    def test_result_not_dict_with_whitelist(self):
        from src.tasks._runner import _filter_result_for_audit

        out = _filter_result_for_audit("just-a-string", {"power_state"})  # type: ignore[arg-type]
        assert out == {
            "emitted": False,
            "reason": "result_not_dict",
            "result_type": "str",
        }


# ── _compute_backoff_delay exponent cap ─────────────────────────────────────


class TestComputeBackoffDelayExponentCap:
    """exp-cap не даёт `2 ** 10000` на повреждённых row'ах с гигантским attempt."""

    def test_cap_at_exponent_16(self):
        from src.tasks._runner import (
            _BACKOFF_EXPONENT_CAP,
            _RETRY_BASE_DELAY_SECONDS,
            _RETRY_MAX_DELAY_SECONDS,
            _compute_backoff_delay,
        )

        # На таком attempt'е exp-cap режет до 2^16, бизнес-cap — до 300s.
        delay = _compute_backoff_delay(10_000)
        assert delay == _RETRY_MAX_DELAY_SECONDS

        # 1.0 — attempt=1 → 2^0 = 1 → 10s
        assert _compute_backoff_delay(1) == _RETRY_BASE_DELAY_SECONDS

        # attempt=2 → 2^1 = 2 → 20s
        assert _compute_backoff_delay(2) == _RETRY_BASE_DELAY_SECONDS * 2

        # attempt=0 / negative → 2^0 = 1 (max(attempt-1, 0))
        assert _compute_backoff_delay(0) == _RETRY_BASE_DELAY_SECONDS
        assert _compute_backoff_delay(-5) == _RETRY_BASE_DELAY_SECONDS

        # Cap'нутый показатель не больше _BACKOFF_EXPONENT_CAP
        assert _BACKOFF_EXPONENT_CAP == 16


# ── extract_bmc_host bare-IPv6 normalization ────────────────────────────────


class TestExtractBmcHostIPv6Normalization:
    """Bare-IPv6 без скобок оборачиваем в `[...]`."""

    def test_bare_ipv6_loopback(self):
        from src.tasks._bmc_helpers import extract_bmc_host

        assert extract_bmc_host("::1") == "[::1]"

    def test_bare_ipv6_full(self):
        from src.tasks._bmc_helpers import extract_bmc_host

        assert extract_bmc_host("2001:db8::1") == "[2001:db8::1]"

    def test_bracketed_ipv6_unchanged(self):
        """Уже-скобочный IPv6 не оборачиваем повторно."""
        from src.tasks._bmc_helpers import extract_bmc_host

        assert extract_bmc_host("[::1]:443") == "[::1]:443"

    def test_ipv4_unchanged(self):
        from src.tasks._bmc_helpers import extract_bmc_host

        assert extract_bmc_host("10.0.0.1") == "10.0.0.1"
        assert extract_bmc_host("10.0.0.1:623") == "10.0.0.1:623"

    def test_hostname_unchanged(self):
        from src.tasks._bmc_helpers import extract_bmc_host

        assert extract_bmc_host("bmc.example.com") == "bmc.example.com"
        assert extract_bmc_host("bmc.example.com:443") == "bmc.example.com:443"

    def test_https_scheme_with_bracketed_ipv6(self):
        from src.tasks._bmc_helpers import extract_bmc_host

        assert extract_bmc_host("https://[2001:db8::1]:443") == "[2001:db8::1]:443"

    def test_empty_string(self):
        from src.tasks._bmc_helpers import extract_bmc_host

        assert extract_bmc_host("") == ""
