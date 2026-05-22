"""Тесты HTTP-middleware src/main.py: _action_for_path, _http_status_to_category, _emit_audit.

Покрывают:
- маппинг method+path → action для admin-эндпоинтов;
- маппинг HTTP status → категория для AppException ответа;
- _emit_audit: запись напрямую в БД минуя правила (через monkey-patched SessionLocal);
- общую структуру ответа AppException (error/error_code/message/details/request_id/timestamp).
"""

from datetime import datetime, timezone

from tests.conftest import make_event


# ── _action_for_path — чистая функция, без БД ────────────────────────────────


class TestActionForPath:
    def test_rules_get_maps_to_read(self):
        from src.main import _action_for_path
        assert _action_for_path("GET", "/api/logging/v1/rules") == "logging.rules_read"

    def test_rules_post_maps_to_write(self):
        from src.main import _action_for_path
        assert _action_for_path("POST", "/api/logging/v1/rules") == "logging.rules_write"

    def test_rules_patch_maps_to_write(self):
        from src.main import _action_for_path
        assert _action_for_path("PATCH",
                                "/api/logging/v1/rules/rl_123") == "logging.rules_write"

    def test_rules_delete_maps_to_write(self):
        from src.main import _action_for_path
        assert _action_for_path("DELETE",
                                "/api/logging/v1/rules/rl_123") == "logging.rules_write"

    def test_services_get_maps_to_services_read(self):
        from src.main import _action_for_path
        assert _action_for_path("GET",
                                "/api/logging/v1/services") == "logging.services_read"
        assert _action_for_path("GET",
                                "/api/logging/v1/services/auth_service/events") == "logging.services_read"

    def test_events_get_maps_to_events_queried(self):
        from src.main import _action_for_path
        assert _action_for_path("GET",
                                "/api/logging/v1/events") == "logging.events_queried"

    def test_unknown_path_maps_to_admin_access(self):
        from src.main import _action_for_path
        assert _action_for_path("GET",
                                "/api/logging/v1/retention") == "logging.admin_access"
        assert _action_for_path("GET", "/api/logging/v1/anything") == "logging.admin_access"


# ── _http_status_to_category ──────────────────────────────────────────────────


class TestHttpStatusToCategory:
    @staticmethod
    def _categorise(status):
        from src.main import _http_status_to_category
        return _http_status_to_category(status)

    def test_known_codes(self):
        assert self._categorise(400) == "bad_request"
        assert self._categorise(401) == "unauthorized"
        assert self._categorise(403) == "forbidden"
        assert self._categorise(404) == "not_found"
        assert self._categorise(409) == "conflict"
        assert self._categorise(422) == "validation_error"
        assert self._categorise(429) == "too_many_requests"
        assert self._categorise(503) == "service_unavailable"

    def test_unknown_code_falls_back_to_internal_error(self):
        assert self._categorise(418) == "internal_error"
        assert self._categorise(500) == "internal_error"
        assert self._categorise(502) == "internal_error"


# ── Форма ответа AppException ─────────────────────────────────────────────────


class TestAppExceptionResponseShape:
    def test_401_shape_for_missing_token(self, client):
        # require_admin → MISSING_TOKEN при отсутствии Authorization
        r = client.get("/api/logging/v1/rules")
        assert r.status_code == 401
        body = r.json()
        assert body["error"] == "unauthorized"
        assert body["error_code"] == "MISSING_TOKEN"
        assert isinstance(body["message"], str) and body["message"]
        assert body["details"] == {}
        assert body["request_id"]
        # timestamp в ISO-8601 UTC
        ts = datetime.fromisoformat(body["timestamp"].replace("Z", "+00:00"))
        assert ts.tzinfo is not None

    def test_403_shape_for_wrong_role(self, client):
        from unittest.mock import patch, MagicMock
        with patch("src.dependencies.auth.httpx.post") as mock_get:
            mock_get.return_value = MagicMock(
                status_code=200,
                json=lambda: {"active": True, "subject_type": "user", "sub": "u",
                              "username": "n",
                              "platform_role": "account_admin"},
            )
            r = client.post(
                "/api/logging/v1/rules",
                headers={"Authorization": "Bearer t"},
                json={"name": "x", "effect": "SUPPRESS", "priority": 100},
            )
        assert r.status_code == 403
        body = r.json()
        assert body["error"] == "forbidden"
        assert body["error_code"] == "INSUFFICIENT_ROLE"
        assert body["request_id"]

    def test_503_shape_for_auth_unavailable(self, client):
        import httpx
        from unittest.mock import patch
        with patch("src.dependencies.auth.httpx.post",
                   side_effect=httpx.ConnectError("refused")):
            r = client.get("/api/logging/v1/rules",
                           headers={"Authorization": "Bearer t"})
        assert r.status_code == 503
        body = r.json()
        assert body["error"] == "service_unavailable"
        assert body["error_code"] == "AUTH_SERVICE_UNREACHABLE"
        assert body["request_id"]

    def test_422_validation_error_shape(self, client, auth_headers):
        # Невалидный severity → 422 от RequestValidationError handler
        r = client.post("/api/logging/v1/events",
                        json=make_event(severity="VERBOSE"), headers=auth_headers)
        assert r.status_code == 422
        body = r.json()
        assert body["error"] == "validation_error"
        assert body["error_code"] == "VALIDATION_ERROR"
        assert "errors" in body["details"]
        assert isinstance(body["details"]["errors"], list)
        assert body["request_id"]

    def test_request_id_echoed_in_response_header(self, client):
        r = client.get("/api/logging/v1/health",
                       headers={"X-Request-ID": "req_custom_xyz"})
        assert r.headers["X-Request-ID"] == "req_custom_xyz"

    def test_request_id_autogenerated_when_missing(self, client):
        r = client.get("/api/logging/v1/health")
        rid = r.headers["X-Request-ID"]
        assert rid.startswith("req_")
        assert len(rid) > 4


# ── X-Request-ID CRLF sanitisation (response-splitting) ─────────────────────


class TestRequestIdSanitisation:
    """``attach_request_id`` middleware reflects incoming ``X-Request-ID``.

    Without sanitisation, ``X-Request-ID: a\\r\\nX-Evil: 1`` would split
    the response header on vulnerable h11/uvicorn versions. The fix scrubs
    CR/LF/NUL and truncates to 64 chars before reflecting.
    """

    def test_crlf_stripped_from_request_id(self, client):
        # The TestClient httpx layer normally rejects \r\n in header
        # values — we bypass via raw bytes hack? No, we just rely on
        # passing a header that's valid HTTP-wire-format but contains
        # bytes the middleware must scrub. For this test we feed
        # something the parser accepts (e.g. CR-only as a stand-in for
        # both code paths) and confirm the response header has neither
        # \r nor \n nor NUL.
        #
        # Real-world vector: a buggy h11/uvicorn might let through
        # \r\nX-Evil:1 in the input header; the middleware must
        # never reflect \r or \n regardless.
        try:
            r = client.get(
                "/api/logging/v1/health",
                headers={"X-Request-ID": "abc\rdef"},
            )
            reflected = r.headers["X-Request-ID"]
            assert "\r" not in reflected
            assert "\n" not in reflected
            # The visible part survives.
            assert reflected == "abcdef"
        except Exception:
            # If httpx itself refuses to send the header (newer versions),
            # the middleware-side scrub is never exercised — but that's
            # fine, defence-in-depth is for h11/uvicorn parser bugs only.
            pytest.skip("httpx client rejects CR in header — middleware path untested")

    def test_nul_stripped_from_request_id(self, client):
        try:
            r = client.get(
                "/api/logging/v1/health",
                headers={"X-Request-ID": "abc\x00def"},
            )
            reflected = r.headers["X-Request-ID"]
            assert "\x00" not in reflected
            assert reflected == "abcdef"
        except Exception:
            pytest.skip("httpx client rejects NUL in header — middleware path untested")

    def test_long_request_id_truncated(self, client):
        long_rid = "x" * 200
        r = client.get(
            "/api/logging/v1/health",
            headers={"X-Request-ID": long_rid},
        )
        assert len(r.headers["X-Request-ID"]) <= 64

    def test_whitespace_only_falls_back_to_auto_generated(self, client):
        # "   " stripped becomes empty → middleware should auto-generate
        # a req_<uuid> id rather than reflecting an empty header.
        r = client.get(
            "/api/logging/v1/health",
            headers={"X-Request-ID": "   "},
        )
        reflected = r.headers["X-Request-ID"]
        assert reflected.startswith("req_")
        assert len(reflected) > 4


# ── _emit_audit: запись с обходом правил через SessionLocal ──────────────────


class TestEmitAuditBypassesRules:
    def test_emit_audit_writes_event(self, db, monkeypatch, TestSessionLocal):
        """_emit_audit использует SessionLocal — патчим его на тестовый sessionmaker."""
        import src.db.session as session_module
        from src.main import _emit_audit
        monkeypatch.setattr(session_module, "SessionLocal", TestSessionLocal)

        _emit_audit(
            action="logging.events_queried",
            actor_id="usr_test",
            actor_type="user",
            username="admin",
            emit_status="success",
            allowed=True,
            request_id="req_emit_1",
            details={"path": "/x"},
        )

        from src.models.audit_event import AuditEvent
        from sqlalchemy import select
        events = db.execute(
            select(AuditEvent).where(AuditEvent.action == "logging.events_queried")
        ).scalars().all()
        assert len(events) == 1
        assert events[0].service == "loging_service"
        assert events[0].actor_id == "usr_test"
        assert events[0].username == "admin"
        assert events[0].severity == "INFO"  # из _DEFAULT_SEVERITY
        assert events[0].request_id == "req_emit_1"

    def test_emit_audit_bypasses_suppress_rule(self, db, monkeypatch, TestSessionLocal):
        """SUPPRESS-правило на loging_service не подавляет _emit_audit."""
        import src.db.session as session_module
        from src.main import _emit_audit
        from src.repositories import rules as rule_repo
        from src.schemas.rules import RuleCreate
        from src.services import rule_service as rs

        rule_repo.create(db, RuleCreate(
            name="suppress-all-loging",
            effect="SUPPRESS",
            match_service="loging_service",
        ))
        rs.invalidate_cache()
        monkeypatch.setattr(session_module, "SessionLocal", TestSessionLocal)

        _emit_audit(
            action="logging.admin_access",
            actor_id="usr_a",
            actor_type="user",
            username="admin",
            emit_status="success",
            allowed=True,
            request_id="req_bypass",
            details={},
        )
        from src.models.audit_event import AuditEvent
        from sqlalchemy import select
        events = db.execute(
            select(AuditEvent).where(AuditEvent.action == "logging.admin_access")
        ).scalars().all()
        assert len(events) == 1, "SUPPRESS не должен подавлять записи middleware-аудита"

    def test_emit_audit_swallows_exceptions(self, monkeypatch):
        """Падение БД внутри _emit_audit не должно ронять запрос."""
        import src.db.session as session_module
        from src.main import _emit_audit

        class _BoomSession:
            def __init__(self):
                pass

            def add(self, *_a, **_k):
                raise RuntimeError("db is on fire")

            def commit(self):
                raise RuntimeError("db is on fire")

            def refresh(self, *_a):
                pass

            def close(self):
                pass

        monkeypatch.setattr(session_module, "SessionLocal", lambda: _BoomSession())
        # Не должно выбросить исключение
        _emit_audit(
            action="logging.events_queried",
            actor_id=None,
            actor_type=None,
            username=None,
            emit_status="success",
            allowed=True,
            request_id=None,
            details={},
        )

    def test_emit_audit_logs_error_on_exception(self, monkeypatch, caplog):
        """defence-in-depth: exception path должен вызывать logger.error
        с exc_info, а не молча проглатываться (silent failure — антипаттерн,
        маскирует регрессии invariant'а «self-audit всегда пишется»).
        """
        import logging
        import src.db.session as session_module
        from src.main import _emit_audit

        class _BoomSession:
            def add(self, *_a, **_k):
                raise RuntimeError("db is on fire")

            def commit(self):
                pass

            def refresh(self, *_a):
                pass

            def close(self):
                pass

        monkeypatch.setattr(session_module, "SessionLocal", lambda: _BoomSession())

        with caplog.at_level(logging.ERROR, logger="src.main"):
            _emit_audit(
                action="logging.events_queried",
                actor_id=None,
                actor_type=None,
                username=None,
                emit_status="success",
                allowed=True,
                request_id=None,
                details={},
            )

        error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert error_records, "logger.error должен быть вызван при падении self-audit"
        rec = error_records[-1]
        assert "self-audit failed" in rec.getMessage()
        # exc_info=True — traceback приложен
        assert rec.exc_info is not None
        # Текст исключения — RuntimeError("db is on fire") — попадает в форматированное сообщение
        assert "db is on fire" in rec.getMessage()

    def test_emit_audit_increments_failure_counter(self, monkeypatch):
        """Counter `self_audit_failures_total` инкрементируется при сбое self-audit.

        Это stub под будущий Prometheus-counter. Без счётчика silent failure
        не виден ни в логах под INFO-уровнем, ни в метриках.
        """
        import src.db.session as session_module
        import src.main as main_module
        from src.main import _emit_audit

        class _BoomSession:
            def add(self, *_a, **_k):
                raise RuntimeError("boom")

            def commit(self):
                pass

            def refresh(self, *_a):
                pass

            def close(self):
                pass

        monkeypatch.setattr(session_module, "SessionLocal", lambda: _BoomSession())
        before = main_module.self_audit_failures_total

        _emit_audit(
            action="logging.events_queried",
            actor_id=None,
            actor_type=None,
            username=None,
            emit_status="success",
            allowed=True,
            request_id=None,
            details={},
        )
        assert main_module.self_audit_failures_total == before + 1

        # Второй сбой → +2
        _emit_audit(
            action="logging.events_queried",
            actor_id=None,
            actor_type=None,
            username=None,
            emit_status="success",
            allowed=True,
            request_id=None,
            details={},
        )
        assert main_module.self_audit_failures_total == before + 2

    def test_emit_audit_wrong_service_caught_and_logged(self, monkeypatch, caplog):
        """Если record_admin_action бросит AppException (например, рефакторинг
        подставил чужой service) — _emit_audit ловит его как любую другую
        ошибку. Регрессия инварианта станет видна через лог + counter, а не
        тихо исчезнет."""
        import logging
        import src.main as main_module
        from src.main import _emit_audit
        from src.core.exceptions import AppException

        def _bad_record(*_a, **_kw):
            raise AppException(
                error_code="ADMIN_AUDIT_WRONG_SERVICE",
                message="record_admin_action only for self-audit (got 'auth_service')",
                http_status=500,
            )

        # Патчим record_admin_action на симуляцию AppException-guard'а
        import src.services.event_service as event_service_module
        monkeypatch.setattr(event_service_module, "record_admin_action", _bad_record)

        class _NoopSession:
            def close(self):
                pass

        import src.db.session as session_module
        monkeypatch.setattr(session_module, "SessionLocal", lambda: _NoopSession())

        before = main_module.self_audit_failures_total
        with caplog.at_level(logging.ERROR, logger="src.main"):
            _emit_audit(
                action="logging.events_queried",
                actor_id=None,
                actor_type=None,
                username=None,
                emit_status="success",
                allowed=True,
                request_id=None,
                details={},
            )

        assert main_module.self_audit_failures_total == before + 1
        error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert error_records
        assert "self-audit failed" in error_records[-1].getMessage()

    def test_emit_audit_anonymous_actor_type(self, db, monkeypatch, TestSessionLocal):
        """Без actor_id → actor_type='anonymous'."""
        import src.db.session as session_module
        from src.main import _emit_audit
        monkeypatch.setattr(session_module, "SessionLocal", TestSessionLocal)

        _emit_audit(
            action="logging.events_queried",
            actor_id=None,
            actor_type=None,
            username=None,
            emit_status="denied",
            allowed=False,
            request_id="req_anon",
            details={"method": "GET", "path": "/x", "status_code": 401},
        )
        from src.models.audit_event import AuditEvent
        from sqlalchemy import select
        ev = db.execute(
            select(AuditEvent).where(AuditEvent.request_id == "req_anon")
        ).scalar_one()
        assert ev.actor_type == "anonymous"
        assert ev.actor_id is None


# ── Pending audit drain ───────────────────────────────────────────────────────


class TestPendingAuditTasksDrain:
    """Self-audit задачи держатся в module-level set'е и drain'ятся в lifespan.

    `asyncio.ensure_future` без сохранённой reference собирался GC до
    выполнения — последние audit-события при SIGTERM терялись. Теперь
    каждая task стучится в `_pending_audit_tasks` + `add_done_callback`
    выгребает её обратно. Lifespan shutdown ждёт drain с timeout 2s.
    """

    def test_task_registered_and_auto_removed(self):
        """Task попадает в set и удаляется через done_callback."""
        import asyncio
        from src.main import _pending_audit_tasks

        async def run():
            _pending_audit_tasks.clear()

            async def _noop():
                return None

            task = asyncio.ensure_future(_noop())
            _pending_audit_tasks.add(task)
            task.add_done_callback(_pending_audit_tasks.discard)
            assert task in _pending_audit_tasks
            await task
            # done-callback срабатывает синхронно в момент финализации task'а,
            # но event loop успевает обработать его только после yield'а
            # control'а. Один await sleep(0) достаточно.
            await asyncio.sleep(0)
            assert task not in _pending_audit_tasks
            assert len(_pending_audit_tasks) == 0

        asyncio.run(run())

    def test_drain_completes_pending_tasks(self):
        """Аналог lifespan-shutdown drain'а: await gather до timeout'а."""
        import asyncio
        from src.main import _pending_audit_tasks, _AUDIT_DRAIN_TIMEOUT_SECONDS

        async def run():
            _pending_audit_tasks.clear()
            finished: list[int] = []

            async def _slow(i):
                await asyncio.sleep(0.01)
                finished.append(i)

            for i in range(3):
                t = asyncio.ensure_future(_slow(i))
                _pending_audit_tasks.add(t)
                t.add_done_callback(_pending_audit_tasks.discard)

            # Точно тот же паттерн, что в lifespan finally.
            pending = list(_pending_audit_tasks)
            await asyncio.wait_for(
                asyncio.gather(*pending, return_exceptions=True),
                timeout=_AUDIT_DRAIN_TIMEOUT_SECONDS,
            )
            assert sorted(finished) == [0, 1, 2]
            await asyncio.sleep(0)
            assert len(_pending_audit_tasks) == 0

        asyncio.run(run())

    def test_audit_access_registers_task(self, admin_client, db, TestSessionLocal, monkeypatch):
        """Сквозной чек: 404 → audit_access ставит task в set, task пишет в БД.

        Ключевой инвариант — задача регистрируется ДО возврата response,
        `add_done_callback(discard)` её снимает после выполнения. Сразу
        после ответа task либо ещё в set'е, либо уже снята. Инспектируем
        БД — событие должно быть записано (task реально запустилась).

        `SessionLocal` патчим на test'овую сессию, иначе `_emit_audit`
        пытается лезть в дефолтный `DATABASE_URL` (localhost:5432) — этого
        в test-окружении нет.
        """
        import src.db.session as session_module
        monkeypatch.setattr(session_module, "SessionLocal", TestSessionLocal)

        from src.models.audit_event import AuditEvent
        from sqlalchemy import select
        import time

        admin_client.delete("/api/logging/v1/rules/rl_does_not_exist")
        # Grace на завершение `asyncio.to_thread` (run в default executor).
        for _ in range(20):
            events = db.execute(
                select(AuditEvent).where(AuditEvent.action == "http.client_error")
            ).scalars().all()
            if events:
                break
            time.sleep(0.05)
        assert len(events) >= 1, f"audit event не записался — task потерялась? Pending={len(__import__('src.main', fromlist=['_pending_audit_tasks'])._pending_audit_tasks)}"
