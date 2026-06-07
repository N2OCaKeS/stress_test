"""Coverage audit wave 6 — loging_service.

Три зоны:
  1. AuditOutbox — непокрытые ветки: invalid params, start idempotency,
     double-full drop, drain_remaining timeout, empty flush, counters, make_envelope,
     write_envelope_to_db actor_type whitelist.
  2. Retention tick helpers — _build_retention_sweep_details (empty/multi snapshot),
     _action_for_path все ветки напрямую.
  3. Per-service-keys-only — empty map → 503, timing-oracle sample_key path,
     whitespace/empty identity, empty-string bearer, SERVICE_IDENTITY_PATH_MISMATCH.
"""

from __future__ import annotations

import asyncio
import json
from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from src.services.audit_outbox import AuditEnvelope, AuditOutbox, make_envelope

from tests._helpers import make_env as _env


# ═══════════════════════════════════════════════════════════════════════════════
# AuditOutbox — непокрытые ветки
# ═══════════════════════════════════════════════════════════════════════════════


class _FakeSession:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0
        self.closes = 0

    def begin_nested(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closes += 1


class TestAuditOutboxInvalidParams:
    """__init__ с невалидными параметрами → ValueError."""

    def test_max_size_zero_raises(self):
        with pytest.raises(ValueError, match="max_size"):
            AuditOutbox(
                max_size=0,
                batch_size=2,
                poll_interval_seconds=0.1,
                session_factory=_FakeSession,
                writer=lambda db, env: None,
                bump_failure=lambda: 0,
            )

    def test_max_size_negative_raises(self):
        with pytest.raises(ValueError, match="max_size"):
            AuditOutbox(
                max_size=-5,
                batch_size=2,
                poll_interval_seconds=0.1,
                session_factory=_FakeSession,
                writer=lambda db, env: None,
                bump_failure=lambda: 0,
            )

    def test_batch_size_zero_raises(self):
        with pytest.raises(ValueError, match="batch_size"):
            AuditOutbox(
                max_size=10,
                batch_size=0,
                poll_interval_seconds=0.1,
                session_factory=_FakeSession,
                writer=lambda db, env: None,
                bump_failure=lambda: 0,
            )

    def test_poll_interval_zero_raises(self):
        with pytest.raises(ValueError, match="poll_interval"):
            AuditOutbox(
                max_size=10,
                batch_size=2,
                poll_interval_seconds=0.0,
                session_factory=_FakeSession,
                writer=lambda db, env: None,
                bump_failure=lambda: 0,
            )

    def test_poll_interval_negative_raises(self):
        with pytest.raises(ValueError, match="poll_interval"):
            AuditOutbox(
                max_size=10,
                batch_size=2,
                poll_interval_seconds=-1.0,
                session_factory=_FakeSession,
                writer=lambda db, env: None,
                bump_failure=lambda: 0,
            )


class TestAuditOutboxStartIdempotency:
    """start() второй раз — молчаливый no-op, task не дублируется."""

    def test_start_twice_does_not_create_second_task(self):
        async def run():
            outbox = AuditOutbox(
                max_size=4,
                batch_size=2,
                poll_interval_seconds=5.0,
                session_factory=_FakeSession,
                writer=lambda db, env: None,
                bump_failure=lambda: 0,
            )
            outbox.start()
            task_first = outbox._drain_task
            outbox.start()  # второй вызов
            task_second = outbox._drain_task
            await outbox.stop(timeout=0.5)
            return task_first, task_second

        t1, t2 = asyncio.run(run())
        assert t1 is t2, "повторный start() должен вернуть тот же task"


class TestAuditOutboxDoubleFull:
    """push_nowait возвращает False когда очередь всё ещё полна после эвикции."""

    def test_double_full_returns_false_and_bumps_dropped_once(self):
        """Симулируем ситуацию когда get_nowait даёт QueueEmpty (drain успел
        опустошить очередь между put и get), а повторный put_nowait снова
        бросает QueueFull. push_nowait возвращает False и
        dropped_overflow_total растёт на 1 — это потеря свежего envelope'а;
        eviction-промах не считается, потому что drain легитимно забрал
        старый элемент. Тест патчит Queue напрямую."""

        async def run():
            outbox = AuditOutbox(
                max_size=2,
                batch_size=1,
                poll_interval_seconds=5.0,
                session_factory=_FakeSession,
                writer=lambda db, env: None,
                bump_failure=lambda: 0,
            )
            outbox._loop = asyncio.get_running_loop()
            outbox._queue = asyncio.Queue(maxsize=2)

            # Заполняем очередь
            outbox._queue.put_nowait(_env("a"))
            outbox._queue.put_nowait(_env("b"))

            # Патчим queue: первый put_nowait бросает QueueFull, get_nowait
            # бросает QueueEmpty (drain забрал), затем повторный put_nowait
            # снова QueueFull.
            call_log: list[str] = []
            original_put = outbox._queue.put_nowait
            original_get = outbox._queue.get_nowait

            put_calls = [0]

            def patched_put(item):
                put_calls[0] += 1
                if put_calls[0] == 1:
                    call_log.append("put-1-full")
                    raise asyncio.QueueFull
                elif put_calls[0] == 2:
                    call_log.append("put-2-full")
                    raise asyncio.QueueFull
                original_put(item)

            def patched_get():
                call_log.append("get-empty")
                raise asyncio.QueueEmpty

            outbox._queue.put_nowait = patched_put
            outbox._queue.get_nowait = patched_get

            result = outbox.push_nowait(_env("new"))
            return result, outbox.dropped_overflow_total(), call_log

        result, dropped, log = asyncio.run(run())
        assert result is False
        assert dropped == 1
        assert "put-1-full" in log
        assert "get-empty" in log
        assert "put-2-full" in log


class TestAuditOutboxDrainRemainingTimeout:
    """_drain_remaining с нулевым бюджетом — события теряются и логируются."""

    def test_drain_remaining_zero_timeout_drops_pending(self):
        captured: list[AuditEnvelope] = []

        def writer(db, env):
            captured.append(env)

        async def run():
            outbox = AuditOutbox(
                max_size=8,
                batch_size=2,
                poll_interval_seconds=10.0,
                session_factory=_FakeSession,
                writer=writer,
                bump_failure=lambda: 0,
            )
            outbox._loop = asyncio.get_running_loop()
            outbox._queue = asyncio.Queue(maxsize=8)

            # Кладём несколько событий без запуска drain-loop
            for i in range(4):
                outbox._queue.put_nowait(_env(f"ev.{i}"))

            # Бюджет = 0 → сразу timeout, все события теряются как
            # shutdown-cause (финальный _drain_remaining).
            await outbox._drain_remaining(timeout=0.0)
            return outbox.dropped_shutdown_total()

        dropped = asyncio.run(run())
        # При timeout=0 и непустой очереди все события считаются потерянными
        assert dropped >= 1


class TestAuditOutboxFlushEmptyBatch:
    """_flush_batch с пустым батчем — early return без обращения к БД."""

    def test_flush_empty_batch_no_session_created(self):
        sessions_created: list[_FakeSession] = []

        def factory():
            s = _FakeSession()
            sessions_created.append(s)
            return s

        outbox = AuditOutbox(
            max_size=4,
            batch_size=2,
            poll_interval_seconds=1.0,
            session_factory=factory,
            writer=lambda db, env: None,
            bump_failure=lambda: 0,
        )

        async def run():
            await outbox._flush_batch([])

        asyncio.run(run())
        assert len(sessions_created) == 0, "пустой батч не должен открывать сессию"


class TestAuditOutboxCounters:
    """drained_total и _reset_counters_for_tests."""

    def test_drained_total_increments_after_drain(self):
        captured: list[AuditEnvelope] = []

        def writer(db, env):
            captured.append(env)

        async def run():
            outbox = AuditOutbox(
                max_size=8,
                batch_size=4,
                poll_interval_seconds=0.01,
                session_factory=_FakeSession,
                writer=writer,
                bump_failure=lambda: 0,
            )
            outbox.start()
            for i in range(3):
                outbox.push_nowait(_env(f"a.{i}"))
            for _ in range(100):
                if outbox.drained_total() >= 3:
                    break
                await asyncio.sleep(0.02)
            await outbox.stop(timeout=1.0)
            return outbox.drained_total()

        total = asyncio.run(run())
        assert total >= 3

    def test_reset_counters_zeroes_both_counters(self):
        async def run():
            outbox = AuditOutbox(
                max_size=2,
                batch_size=1,
                poll_interval_seconds=5.0,
                session_factory=_FakeSession,
                writer=lambda db, env: None,
                bump_failure=lambda: 0,
            )
            outbox._loop = asyncio.get_running_loop()
            outbox._queue = asyncio.Queue(maxsize=2)

            # Переполнение — dropped_overflow_total += 1
            outbox._queue.put_nowait(_env("a"))
            outbox._queue.put_nowait(_env("b"))
            outbox.push_nowait(_env("c"))

            # Сбрасываем
            outbox._reset_counters_for_tests()
            return (
                outbox.dropped_overflow_total(),
                outbox.dropped_cancel_total(),
                outbox.dropped_shutdown_total(),
                outbox.drained_total(),
            )

        overflow, cancel, shutdown, drained = asyncio.run(run())
        assert overflow == 0
        assert cancel == 0
        assert shutdown == 0
        assert drained == 0


class TestMakeEnvelopeUTC:
    """make_envelope фиксирует enqueued_at в UTC."""

    def test_enqueued_at_is_utc(self):
        env = _env("test.action")
        assert env.enqueued_at.tzinfo is not None
        # UTC offset должен быть нулевым
        assert env.enqueued_at.utcoffset().total_seconds() == 0

    def test_fields_round_trip(self):
        env = make_envelope(
            action="user.login",
            actor_id="usr_abc",
            actor_type="user",
            username="alice",
            emit_status="success",
            allowed=True,
            request_id="req_xyz",
            details={"k": "v"},
        )
        assert env.action == "user.login"
        assert env.actor_id == "usr_abc"
        assert env.actor_type == "user"
        assert env.username == "alice"
        assert env.emit_status == "success"
        assert env.allowed is True
        assert env.request_id == "req_xyz"
        assert env.details == {"k": "v"}


class TestWriteEnvelopeToDbActorTypeWhitelist:
    """write_envelope_to_db резолвит actor_type через whitelist."""

    def test_unknown_actor_type_resolved_to_anonymous(self):
        from src.services.audit_outbox import write_envelope_to_db

        resolved: list[str] = []

        def fake_record_admin(db, payload, *, commit=True):
            resolved.append(payload.actor_type)
            return MagicMock()

        env = make_envelope(
            action="logging.events_queried",
            actor_id="usr_1",
            actor_type="robot",  # вне whitelist'а
            username="svc",
            emit_status="success",
            allowed=True,
            request_id=None,
            details={},
        )

        with patch("src.services.event_service.record_admin_action", fake_record_admin):
            write_envelope_to_db(_FakeSession(), env)

        assert resolved == ["anonymous"]

    def test_known_actor_type_preserved(self):
        from src.services.audit_outbox import write_envelope_to_db

        resolved: list[str] = []

        def fake_record_admin(db, payload, *, commit=True):
            resolved.append(payload.actor_type)
            return MagicMock()

        for actor_type in ("user", "bot", "service", "anonymous", "oauth_client"):
            resolved.clear()
            env = make_envelope(
                action="logging.events_queried",
                actor_id=None,
                actor_type=actor_type,
                username=None,
                emit_status="success",
                allowed=True,
                request_id=None,
                details={},
            )
            with patch("src.services.event_service.record_admin_action", fake_record_admin):
                write_envelope_to_db(_FakeSession(), env)
            assert resolved == [actor_type], f"actor_type={actor_type!r} должен проходить без изменений"

    def test_none_actor_type_resolved_to_anonymous(self):
        from src.services.audit_outbox import write_envelope_to_db

        resolved: list[str] = []

        def fake_record_admin(db, payload, *, commit=True):
            resolved.append(payload.actor_type)
            return MagicMock()

        env = make_envelope(
            action="logging.events_queried",
            actor_id=None,
            actor_type=None,
            username=None,
            emit_status="success",
            allowed=True,
            request_id=None,
            details={},
        )
        with patch("src.services.event_service.record_admin_action", fake_record_admin):
            write_envelope_to_db(_FakeSession(), env)
        assert resolved == ["anonymous"]


class TestAuditOutboxFallbackFailure:
    """Fallback (без loop) — ошибка writer'а инкрементит bump_failure."""

    def test_fallback_writer_exception_bumps_failure(self):
        bumps: list[int] = []
        call_n = [0]

        def bump():
            n = len(bumps) + 1
            bumps.append(n)
            return n

        def failing_writer(db, env):
            call_n[0] += 1
            raise RuntimeError("kaboom")

        outbox = AuditOutbox(
            max_size=4,
            batch_size=2,
            poll_interval_seconds=1.0,
            session_factory=_FakeSession,
            writer=failing_writer,
            bump_failure=bump,
        )
        # Нет start() → queue=None → fallback path
        result = outbox.push_nowait(_env("fail"))
        assert result is False
        assert len(bumps) == 1


# ═══════════════════════════════════════════════════════════════════════════════
# Retention tick helpers
# ═══════════════════════════════════════════════════════════════════════════════


class TestBuildRetentionSweepDetails:
    """_build_retention_sweep_details — пустой snapshot и мульти-snapshot."""

    def test_empty_snapshot_no_min_max_keys(self):
        from src.main import _build_retention_sweep_details

        details = _build_retention_sweep_details(
            deleted=0,
            snapshot=[],
            run_date_msk=date.today().isoformat(),
        )
        assert details["deleted_count"] == 0
        assert details["run_date_msk"] == date.today().isoformat()
        assert details["policies"] == []
        assert "min_retain_days" not in details
        assert "max_retain_days" not in details

    def test_single_policy_snapshot_min_max_equal(self):
        from src.main import _build_retention_sweep_details

        policy = MagicMock()
        policy.id = "rp_aaa"
        policy.retain_days = 90
        policy.severity = None
        policy.service = None

        details = _build_retention_sweep_details(
            deleted=42,
            snapshot=[policy],
            run_date_msk=date.today().isoformat(),
        )
        assert details["deleted_count"] == 42
        assert details["min_retain_days"] == 90
        assert details["max_retain_days"] == 90
        assert len(details["policies"]) == 1
        assert details["policies"][0]["retain_days"] == 90

    def test_multi_policy_snapshot_correct_min_max(self):
        from src.main import _build_retention_sweep_details

        def _p(retain_days, policy_id):
            p = MagicMock()
            p.id = policy_id
            p.retain_days = retain_days
            p.severity = None
            p.service = None
            return p

        snapshot = [_p(30, "rp_1"), _p(365, "rp_2"), _p(180, "rp_3")]
        details = _build_retention_sweep_details(
            deleted=100,
            snapshot=snapshot,
            run_date_msk=date.today().isoformat(),
        )
        assert details["min_retain_days"] == 30
        assert details["max_retain_days"] == 365
        assert len(details["policies"]) == 3

    def test_severity_and_service_appear_in_policy_details(self):
        from src.main import _build_retention_sweep_details

        policy = MagicMock()
        policy.id = "rp_bbb"
        policy.retain_days = 60
        policy.severity = "ERROR"
        policy.service = "auth_service"

        details = _build_retention_sweep_details(
            deleted=5,
            snapshot=[policy],
            run_date_msk=date.today().isoformat(),
        )
        assert details["policies"][0]["severity"] == "ERROR"
        assert details["policies"][0]["service"] == "auth_service"

    def test_deleted_count_is_int(self):
        """deleted_count явно конвертируется в int."""
        from src.main import _build_retention_sweep_details

        details = _build_retention_sweep_details(
            deleted=7,
            snapshot=[],
            run_date_msk=date.today().isoformat(),
        )
        assert isinstance(details["deleted_count"], int)


class TestActionForPath:
    """_action_for_path — все ветки напрямую."""

    def test_rules_get_returns_rules_read(self):
        from src.main import _action_for_path
        assert _action_for_path("GET", "/api/logging/v1/rules") == "logging.rules_read"

    def test_rules_post_returns_rules_write(self):
        from src.main import _action_for_path
        assert _action_for_path("POST", "/api/logging/v1/rules") == "logging.rules_write"

    def test_rules_patch_returns_rules_write(self):
        from src.main import _action_for_path
        assert _action_for_path("PATCH", "/api/logging/v1/rules/rl_123") == "logging.rules_write"

    def test_rules_delete_returns_rules_write(self):
        from src.main import _action_for_path
        assert _action_for_path("DELETE", "/api/logging/v1/rules/rl_123") == "logging.rules_write"

    def test_events_get_returns_events_queried(self):
        from src.main import _action_for_path
        assert _action_for_path("GET", "/api/logging/v1/events") == "logging.events_queried"

    def test_services_events_path_returns_service_events_browsed(self):
        """Путь /services/{svc}/events — каталог action'ов сервиса,
        отдельный action `logging.service_events_browsed` (не audit-журнал)."""
        from src.main import _action_for_path
        result = _action_for_path("GET", "/api/logging/v1/services/auth_service/events")
        assert result == "logging.service_events_browsed"

    def test_services_path_without_events_returns_services_read(self):
        from src.main import _action_for_path
        assert _action_for_path("GET", "/api/logging/v1/services") == "logging.services_read"

    def test_retention_get_returns_retention_read(self):
        from src.main import _action_for_path
        assert _action_for_path("GET", "/api/logging/v1/retention") == "logging.retention_read"

    def test_retention_put_returns_retention_write(self):
        from src.main import _action_for_path
        assert _action_for_path("PUT", "/api/logging/v1/retention") == "logging.retention_write"

    def test_retention_delete_returns_retention_write(self):
        from src.main import _action_for_path
        assert _action_for_path("DELETE", "/api/logging/v1/retention") == "logging.retention_write"

    def test_unknown_path_returns_admin_access(self):
        from src.main import _action_for_path
        assert _action_for_path("GET", "/api/logging/v1/unknown") == "logging.admin_access"

    def test_health_path_returns_admin_access(self):
        """health/ready не попадают в audit_access (скипаются раньше),
        но _action_for_path сам ничего не знает про skip — fallback admin_access."""
        from src.main import _action_for_path
        result = _action_for_path("GET", "/api/logging/v1/health")
        assert result == "logging.admin_access"


# ═══════════════════════════════════════════════════════════════════════════════
# Per-service-keys-only — непокрытые ветки
# ═══════════════════════════════════════════════════════════════════════════════


class TestRequireServiceTokenEmptyMap:
    """Пустой SERVICE_API_KEYS → 503 SERVICE_TOKEN_NOT_CONFIGURED на ingest."""

    def test_empty_map_returns_503_on_ingest(self, monkeypatch, db):
        from fastapi.testclient import TestClient
        from src.core.config import get_settings
        from src.dependencies.db import get_db
        from src.main import app
        from tests.conftest import make_event

        monkeypatch.setenv("SERVICE_API_KEYS", json.dumps({}))
        monkeypatch.setenv("INTROSPECT_SERVICE_API_KEY", "test-introspect-key")
        get_settings.cache_clear()

        def _override_db():
            try:
                yield db
            finally:
                pass

        app.dependency_overrides[get_db] = _override_db
        try:
            with TestClient(app) as c:
                r = c.post(
                    "/api/logging/v1/events",
                    json=make_event(),
                    headers={
                        "Authorization": "Bearer some-key",
                        "X-Service-Identity": "auth_service",
                    },
                )
        finally:
            app.dependency_overrides.clear()
            get_settings.cache_clear()

        assert r.status_code == 503
        assert r.json()["error_code"] == "SERVICE_TOKEN_NOT_CONFIGURED"

    def test_empty_map_returns_503_on_register_events(self, monkeypatch, db):
        from fastapi.testclient import TestClient
        from src.core.config import get_settings
        from src.dependencies.db import get_db
        from src.main import app
        from tests.conftest import make_event_def

        monkeypatch.setenv("SERVICE_API_KEYS", json.dumps({}))
        monkeypatch.setenv("INTROSPECT_SERVICE_API_KEY", "test-introspect-key")
        get_settings.cache_clear()

        def _override_db():
            try:
                yield db
            finally:
                pass

        app.dependency_overrides[get_db] = _override_db
        try:
            with TestClient(app) as c:
                r = c.post(
                    "/api/logging/v1/services/auth_service/events",
                    json={"events": [make_event_def()]},
                    headers={
                        "Authorization": "Bearer some-key",
                        "X-Service-Identity": "auth_service",
                    },
                )
        finally:
            app.dependency_overrides.clear()
            get_settings.cache_clear()

        assert r.status_code == 503
        assert r.json()["error_code"] == "SERVICE_TOKEN_NOT_CONFIGURED"


class TestRequireServiceTokenTimingOracle:
    """Timing-oracle: identity вне map'а всё равно прогоняет compare_digest."""

    def test_unknown_identity_runs_compare_digest_then_401(self, monkeypatch, db):
        """Подтверждаем что unknown identity → 401 INVALID_SERVICE_KEY (не 403/404),
        и что ветка sample_key отрабатывает без исключений.

        Тест проверяет HTTP-контракт; timing-behavior самой compare_digest
        гарантируется stdlib, её константное время — вне scope unit-теста."""
        from fastapi.testclient import TestClient
        from src.core.config import get_settings
        from src.dependencies.db import get_db
        from src.main import app
        from tests.conftest import make_event

        monkeypatch.setenv(
            "SERVICE_API_KEYS",
            json.dumps({"server_service": "real-secret-key"}),
        )
        monkeypatch.setenv("INTROSPECT_SERVICE_API_KEY", "test-introspect-key")
        get_settings.cache_clear()

        def _override_db():
            try:
                yield db
            finally:
                pass

        app.dependency_overrides[get_db] = _override_db
        try:
            with TestClient(app) as c:
                # Предъявляем identity, которой нет в map'е
                r = c.post(
                    "/api/logging/v1/events",
                    json=make_event(service="auth_service"),
                    headers={
                        "Authorization": "Bearer real-secret-key",
                        "X-Service-Identity": "auth_service",
                    },
                )
        finally:
            app.dependency_overrides.clear()
            get_settings.cache_clear()

        assert r.status_code == 401
        assert r.json()["error_code"] == "INVALID_SERVICE_KEY"

    def test_single_entry_map_sample_key_from_first_value(self, monkeypatch, db):
        """При одном entry в map sample_key берётся из него; unknown identity
        сверяется с ним через compare_digest (тест контракта, не timing)."""
        from fastapi.testclient import TestClient
        from src.core.config import get_settings
        from src.dependencies.db import get_db
        from src.main import app
        from tests.conftest import make_event

        monkeypatch.setenv(
            "SERVICE_API_KEYS",
            json.dumps({"server_service": "only-key"}),
        )
        monkeypatch.setenv("INTROSPECT_SERVICE_API_KEY", "test-introspect-key")
        get_settings.cache_clear()

        def _override_db():
            try:
                yield db
            finally:
                pass

        app.dependency_overrides[get_db] = _override_db
        try:
            with TestClient(app) as c:
                r = c.post(
                    "/api/logging/v1/events",
                    json=make_event(service="config_service"),
                    headers={
                        "Authorization": "Bearer only-key",
                        "X-Service-Identity": "config_service",
                    },
                )
        finally:
            app.dependency_overrides.clear()
            get_settings.cache_clear()

        assert r.status_code == 401
        assert r.json()["error_code"] == "INVALID_SERVICE_KEY"


class TestRequireServiceTokenEmptyIdentity:
    """Пустая / whitespace-only identity нормализуется в '<empty>' → не в map → 401."""

    def test_whitespace_identity_rejected(self, monkeypatch, db):
        from fastapi.testclient import TestClient
        from src.core.config import get_settings
        from src.dependencies.db import get_db
        from src.main import app
        from tests.conftest import make_event

        monkeypatch.setenv(
            "SERVICE_API_KEYS",
            json.dumps({"auth_service": "k1"}),
        )
        monkeypatch.setenv("INTROSPECT_SERVICE_API_KEY", "test-introspect-key")
        get_settings.cache_clear()

        def _override_db():
            try:
                yield db
            finally:
                pass

        app.dependency_overrides[get_db] = _override_db
        try:
            with TestClient(app) as c:
                r = c.post(
                    "/api/logging/v1/events",
                    json=make_event(),
                    headers={
                        "Authorization": "Bearer k1",
                        "X-Service-Identity": "   ",  # только пробелы
                    },
                )
        finally:
            app.dependency_overrides.clear()
            get_settings.cache_clear()

        assert r.status_code == 401
        assert r.json()["error_code"] == "INVALID_SERVICE_KEY"

    def test_empty_string_identity_rejected(self, monkeypatch, db):
        from fastapi.testclient import TestClient
        from src.core.config import get_settings
        from src.dependencies.db import get_db
        from src.main import app
        from tests.conftest import make_event

        monkeypatch.setenv(
            "SERVICE_API_KEYS",
            json.dumps({"auth_service": "k1"}),
        )
        monkeypatch.setenv("INTROSPECT_SERVICE_API_KEY", "test-introspect-key")
        get_settings.cache_clear()

        def _override_db():
            try:
                yield db
            finally:
                pass

        app.dependency_overrides[get_db] = _override_db
        try:
            with TestClient(app) as c:
                r = c.post(
                    "/api/logging/v1/events",
                    json=make_event(),
                    headers={
                        "Authorization": "Bearer k1",
                        "X-Service-Identity": "",
                    },
                )
        finally:
            app.dependency_overrides.clear()
            get_settings.cache_clear()

        # Пустой header может быть проигнорирован HTTP-клиентом или трактоваться
        # как отсутствующий → 401 (MISSING_SERVICE_IDENTITY или INVALID_SERVICE_KEY)
        assert r.status_code == 401


class TestServiceIdentityPathMismatch:
    """X-Service-Identity не совпадает с path-параметром → 403."""

    def test_identity_mismatch_path_returns_403(self, client, auth_headers):
        """auth_service пытается зарегистрировать события под server_service."""
        from tests.conftest import make_event_def

        r = client.post(
            "/api/logging/v1/services/server_service/events",
            json={"events": [make_event_def()]},
            # auth_headers несёт X-Service-Identity: auth_service
            headers=auth_headers,
        )
        assert r.status_code == 403
        assert r.json()["error_code"] == "SERVICE_IDENTITY_PATH_MISMATCH"

    def test_matching_identity_and_path_ok(self, client, auth_headers):
        """auth_service регистрирует события под auth_service → 200."""
        from tests.conftest import make_event_def

        r = client.post(
            "/api/logging/v1/services/auth_service/events",
            json={"events": [make_event_def()]},
            headers=auth_headers,
        )
        assert r.status_code == 200

    def test_server_service_identity_registers_server_service_events(self, client, auth_headers):
        """server_service identity → регистрирует события для server_service."""
        from tests.conftest import make_event_def

        server_headers = dict(auth_headers)
        server_headers["X-Service-Identity"] = "server_service"

        r = client.post(
            "/api/logging/v1/services/server_service/events",
            json={"events": [make_event_def(action="server.power_on")]},
            headers=server_headers,
        )
        assert r.status_code == 200

    def test_identity_mismatch_with_normalization(self, client, auth_headers):
        """auth_service identity vs server_service path → mismatch даже после нормализации."""
        from tests.conftest import make_event_def

        # Даём server_service в path, но identity остаётся auth_service
        r = client.post(
            "/api/logging/v1/services/SERVER_SERVICE/events",
            json={"events": [make_event_def()]},
            headers=auth_headers,
        )
        # SERVER_SERVICE нормализуется в server_service ≠ auth_service → 403
        assert r.status_code == 403
        assert r.json()["error_code"] in (
            "SERVICE_IDENTITY_PATH_MISMATCH",
            "RESERVED_SERVICE_NAME",
            "VALIDATION_ERROR",
        )


class TestRequireServiceTokenNoBearerHeader:
    """Нет Authorization header → 401 INVALID_SERVICE_KEY."""

    def test_no_auth_header_returns_401(self, client):
        from tests.conftest import make_event

        r = client.post(
            "/api/logging/v1/events",
            json=make_event(),
            headers={"X-Service-Identity": "auth_service"},
        )
        assert r.status_code == 401
        assert r.json()["error_code"] == "INVALID_SERVICE_KEY"
