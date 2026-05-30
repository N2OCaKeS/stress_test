"""Tests for the batch-2 fixes.

Each test class covers exactly one item:

* ``TestActorTypePropagation`` — `_fetch_identity` propagates
  `subject_type` from auth_service introspect into `identity["actor_type"]`;
  ``main._emit_audit`` uses it instead of the hard-coded "user".
* ``TestIngestIdempotency`` — ``EventCreate.idempotency_key`` +
  partial UNIQUE index on `(service, idempotency_key)` + `ON CONFLICT DO
  NOTHING` dedup in `repositories/events.insert`. Outbox-retry safe.
* ``TestPerServiceApiKeys`` — `SERVICE_API_KEYS` JSON env enables
  per-identity bearer secrets in `require_service_token`; legacy
  ``SERVICE_API_KEY`` fallback preserved.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime, timezone

import httpx
import pytest

from tests.conftest import make_event


@pytest.fixture(autouse=True)
def _reset_rate_limit_storage():
    """Reset slowapi's in-memory counter between tests in this file.

    The module-level ``Limiter`` in ``src.main`` shares state across
    tests; without a reset, the per-IP bucket (``testclient`` → 100/min)
    accumulates across this file's POSTs and leaks into the next file,
    triggering 429 on unrelated tests. Mirrors the pattern used in
    ``tests/test_rate_limit.py`` (not yet conftest-wide).
    """
    yield
    from src.main import limiter
    limiter.reset()


# ── _fetch_identity propagates actor_type ───────────────────────────────────


class TestActorTypePropagation:
    """auth_service introspect returns ``subject_type`` ∈ {user, bot, oauth_client}.

    Before the fix, ``_fetch_identity`` ignored it and ``_emit_audit`` wrote
    ``actor_type="user"`` unconditionally — every M2M call (PAT, bot,
    oauth_client) looked like a fake user in the SOC.

    These tests focus on the propagation surface (``_fetch_identity`` →
    ``identity["actor_type"]``) and call ``_emit_audit`` directly with the
    resolved value to avoid the asyncio.ensure_future race in the
    ``audit_access`` middleware.
    """

    @staticmethod
    @contextmanager
    def _patch_introspect(subject_type: str | None, sub: str):
        body: dict = {
            "active": True,
            "sub": sub,
            "username": "test-actor",
            "platform_role": None,
        }
        if subject_type is not None:
            body["subject_type"] = subject_type

        from src.dependencies import auth as _auth_deps

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=body)

        pooled = httpx.AsyncClient(
            base_url="http://auth-test:8000",
            transport=httpx.MockTransport(handler),
            timeout=5.0,
        )
        original = _auth_deps._introspect_client
        _auth_deps._introspect_client = pooled
        try:
            yield
        finally:
            _auth_deps._introspect_client = original
            import asyncio
            asyncio.run(pooled.aclose())

    def _resolve_identity(self, client, subject_type: str | None, sub: str) -> dict:
        """Drive the FastAPI dependency chain and capture ``request.state.auth_identity``."""
        import asyncio

        from src.dependencies.auth import _fetch_identity
        from fastapi.security import HTTPAuthorizationCredentials

        captured: dict = {}

        class _MockRequest:
            def __init__(self) -> None:
                self.headers: dict[str, str] = {}

                class _State:
                    pass

                self.state = _State()

        request = _MockRequest()
        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="t")

        with self._patch_introspect(subject_type, sub):
            identity = asyncio.run(_fetch_identity(creds, request))
        captured.update(identity)
        return captured

    def test_user_subject_type_recorded(self, client, db, monkeypatch, TestSessionLocal):
        from src.models.audit_event import AuditEvent
        from sqlalchemy import select
        import src.db.session as session_module
        from src.main import _emit_audit

        identity = self._resolve_identity(client, "user", "usr_alice")
        assert identity["actor_type"] == "user"

        # Drive _emit_audit synchronously with the resolved actor_type to
        # avoid the asyncio.ensure_future race in audit_access middleware.
        monkeypatch.setattr(session_module, "SessionLocal", TestSessionLocal)
        _emit_audit(
            action="http.access_denied",
            actor_id=identity["user_id"],
            actor_type=identity["actor_type"],
            username=identity.get("username"),
            emit_status="denied",
            allowed=False,
            request_id="req_test_user",
            details={"method": "GET", "path": "/x", "status_code": 401},
        )

        ev = db.execute(
            select(AuditEvent).where(AuditEvent.request_id == "req_test_user")
        ).scalar_one()
        assert ev.actor_type == "user"
        assert ev.actor_id == "usr_alice"

    def test_bot_subject_type_recorded(self, client, db, monkeypatch, TestSessionLocal):
        from src.models.audit_event import AuditEvent
        from sqlalchemy import select
        import src.db.session as session_module
        from src.main import _emit_audit

        identity = self._resolve_identity(client, "bot", "bot_runner_42")
        assert identity["actor_type"] == "bot"

        monkeypatch.setattr(session_module, "SessionLocal", TestSessionLocal)
        _emit_audit(
            action="http.access_denied",
            actor_id=identity["user_id"],
            actor_type=identity["actor_type"],
            username=identity.get("username"),
            emit_status="denied",
            allowed=False,
            request_id="req_test_bot",
            details={"method": "GET", "path": "/x", "status_code": 401},
        )

        ev = db.execute(
            select(AuditEvent).where(AuditEvent.request_id == "req_test_bot")
        ).scalar_one()
        assert ev.actor_type == "bot"
        assert ev.actor_id == "bot_runner_42"

    def test_oauth_client_subject_type_recorded(self, client, db, monkeypatch, TestSessionLocal):
        from src.models.audit_event import AuditEvent
        from sqlalchemy import select
        import src.db.session as session_module
        from src.main import _emit_audit

        identity = self._resolve_identity(client, "oauth_client", "cli_abc")
        assert identity["actor_type"] == "oauth_client"

        monkeypatch.setattr(session_module, "SessionLocal", TestSessionLocal)
        _emit_audit(
            action="http.access_denied",
            actor_id=identity["user_id"],
            actor_type=identity["actor_type"],
            username=identity.get("username"),
            emit_status="denied",
            allowed=False,
            request_id="req_test_oauth",
            details={"method": "GET", "path": "/x", "status_code": 401},
        )

        ev = db.execute(
            select(AuditEvent).where(AuditEvent.request_id == "req_test_oauth")
        ).scalar_one()
        assert ev.actor_type == "oauth_client"
        assert ev.actor_id == "cli_abc"

    def test_missing_subject_type_falls_back_to_user(self, client):
        """Older introspect (no subject_type) → fallback "user"."""
        identity = self._resolve_identity(client, None, "usr_legacy")
        assert identity["actor_type"] == "user"

    def test_emit_audit_unknown_actor_type_falls_back(
        self, db, monkeypatch, TestSessionLocal
    ):
        """`_emit_audit` пишет `"anonymous"` для любого unknown actor_type.

        Раньше `actor_type=None + actor_id != None → "user"`; SOC получал бы
        фейкового юзера для bot/oauth_client с неизвестным subject_type.
        Теперь любой не-whitelist'нутый actor_type → `"anonymous"`.
        """
        from src.models.audit_event import AuditEvent
        from sqlalchemy import select
        import src.db.session as session_module
        from src.main import _emit_audit

        monkeypatch.setattr(session_module, "SessionLocal", TestSessionLocal)
        # actor_type=None + actor_id set → "anonymous" (не «user»)
        _emit_audit(
            action="http.access_denied",
            actor_id="usr_x",
            actor_type=None,
            username=None,
            emit_status="denied",
            allowed=False,
            request_id="req_fallback_user",
            details={},
        )
        ev = db.execute(
            select(AuditEvent).where(AuditEvent.request_id == "req_fallback_user")
        ).scalar_one()
        assert ev.actor_type == "anonymous"

        # actor_type=None + actor_id=None → fallback "anonymous"
        _emit_audit(
            action="http.access_denied",
            actor_id=None,
            actor_type=None,
            username=None,
            emit_status="denied",
            allowed=False,
            request_id="req_fallback_anon",
            details={},
        )
        ev = db.execute(
            select(AuditEvent).where(AuditEvent.request_id == "req_fallback_anon")
        ).scalar_one()
        assert ev.actor_type == "anonymous"


# ── idempotency_key dedup on POST /events ───────────────────────────────────


class TestIngestIdempotency:
    """`(service, idempotency_key)` partial UNIQUE → ON CONFLICT DO NOTHING.

    Outbox-retry safe: two POSTs with the same key write a single row.
    """

    def test_two_posts_same_key_dedupe(self, client, auth_headers, db):
        """Same (service, key) twice → 201 both times, 1 row, same event_id."""
        from sqlalchemy import select
        from src.models.audit_event import AuditEvent

        payload = make_event(idempotency_key="batch-001")
        r1 = client.post("/api/logging/v1/events", json=payload, headers=auth_headers)
        assert r1.status_code == 201
        event_id_1 = r1.json()["id"]

        r2 = client.post("/api/logging/v1/events", json=payload, headers=auth_headers)
        assert r2.status_code == 201
        event_id_2 = r2.json()["id"]

        # Same canonical row.
        assert event_id_1 == event_id_2

        # Only one row in DB.
        rows = db.execute(
            select(AuditEvent).where(
                AuditEvent.service == "auth_service",
                AuditEvent.idempotency_key == "batch-001",
            )
        ).scalars().all()
        assert len(rows) == 1

    def test_different_keys_create_two_rows(self, client, auth_headers, db):
        """Different idempotency_key values → both rows kept."""
        from sqlalchemy import select
        from src.models.audit_event import AuditEvent

        for key in ("k-a", "k-b"):
            r = client.post(
                "/api/logging/v1/events",
                json=make_event(idempotency_key=key),
                headers=auth_headers,
            )
            assert r.status_code == 201

        rows = db.execute(
            select(AuditEvent).where(AuditEvent.service == "auth_service")
        ).scalars().all()
        assert len(rows) == 2

    def test_no_key_inserts_unconditionally(self, client, auth_headers, db):
        """Legacy ingest without idempotency_key → ON CONFLICT path skipped."""
        from sqlalchemy import select
        from src.models.audit_event import AuditEvent

        for _ in range(2):
            r = client.post(
                "/api/logging/v1/events",
                json=make_event(),  # no idempotency_key
                headers=auth_headers,
            )
            assert r.status_code == 201

        rows = db.execute(
            select(AuditEvent).where(AuditEvent.service == "auth_service")
        ).scalars().all()
        assert len(rows) == 2

    def test_same_key_different_service_namespaces(self, client, auth_headers, db):
        """Different `service`, same key → 2 rows (key is scoped per service)."""
        from sqlalchemy import select
        from src.models.audit_event import AuditEvent

        for svc in ("auth_service", "server_service"):
            r = client.post(
                "/api/logging/v1/events",
                json=make_event(service=svc, idempotency_key="shared-key"),
                headers=auth_headers,
            )
            assert r.status_code == 201

        rows = db.execute(
            select(AuditEvent).where(
                AuditEvent.idempotency_key == "shared-key"
            )
        ).scalars().all()
        assert len(rows) == 2
        assert {r.service for r in rows} == {"auth_service", "server_service"}


# ── per-service API keys ────────────────────────────────────────────────────


class TestPerServiceApiKeys:
    """SERVICE_API_KEYS map: per-identity bearer secrets — единственный режим.

      * `X-Service-Identity` обязателен и работает ключом lookup'а.
      * Identity не в map'е → 401 INVALID_SERVICE_KEY.
      * Key mismatch → 401 INVALID_SERVICE_KEY.
      * Missing identity header → 401 MISSING_SERVICE_IDENTITY.
      * Пустой `SERVICE_API_KEYS` → 503 SERVICE_TOKEN_NOT_CONFIGURED
        (legacy single-key fallback убран).
    """

    def test_per_service_key_matches(self, monkeypatch, db):
        from fastapi.testclient import TestClient
        from src.core.config import get_settings
        from src.dependencies.db import get_db
        from src.main import app

        monkeypatch.setenv(
            "SERVICE_API_KEYS",
            json.dumps({"server_service": "k1"}),
        )
        get_settings.cache_clear()

        def _override_db():
            try:
                yield db
            finally:
                pass

        app.dependency_overrides[get_db] = _override_db
        try:
            with TestClient(app) as c:
                # Force fallback path off (pooled httpx not relevant here).
                from src.dependencies import auth as _auth
                _pool = _auth._introspect_client
                _auth._introspect_client = None
                try:
                    r = c.post(
                        "/api/logging/v1/events",
                        json=make_event(service="server_service"),
                        headers={
                            "Authorization": "Bearer k1",
                            "X-Service-Identity": "server_service",
                        },
                    )
                finally:
                    _auth._introspect_client = _pool
        finally:
            app.dependency_overrides.clear()
            get_settings.cache_clear()

        assert r.status_code == 201, r.text

    def test_per_service_key_wrong_secret_rejected(self, monkeypatch, db):
        from fastapi.testclient import TestClient
        from src.core.config import get_settings
        from src.dependencies.db import get_db
        from src.main import app

        monkeypatch.setenv(
            "SERVICE_API_KEYS",
            json.dumps({"server_service": "k1"}),
        )
        get_settings.cache_clear()

        def _override_db():
            try:
                yield db
            finally:
                pass

        app.dependency_overrides[get_db] = _override_db
        try:
            with TestClient(app) as c:
                from src.dependencies import auth as _auth
                _pool = _auth._introspect_client
                _auth._introspect_client = None
                try:
                    r = c.post(
                        "/api/logging/v1/events",
                        json=make_event(service="server_service"),
                        headers={
                            "Authorization": "Bearer k2",
                            "X-Service-Identity": "server_service",
                        },
                    )
                finally:
                    _auth._introspect_client = _pool
        finally:
            app.dependency_overrides.clear()
            get_settings.cache_clear()

        assert r.status_code == 401
        assert r.json()["error_code"] == "INVALID_SERVICE_KEY"

    def test_unknown_identity_rejected(self, monkeypatch, db):
        """Identity not in SERVICE_API_KEYS → 401 INVALID_SERVICE_KEY."""
        from fastapi.testclient import TestClient
        from src.core.config import get_settings
        from src.dependencies.db import get_db
        from src.main import app

        monkeypatch.setenv(
            "SERVICE_API_KEYS",
            json.dumps({"server_service": "k1"}),
        )
        get_settings.cache_clear()

        def _override_db():
            try:
                yield db
            finally:
                pass

        app.dependency_overrides[get_db] = _override_db
        try:
            with TestClient(app) as c:
                from src.dependencies import auth as _auth
                _pool = _auth._introspect_client
                _auth._introspect_client = None
                try:
                    r = c.post(
                        "/api/logging/v1/events",
                        json=make_event(service="auth_service"),
                        headers={
                            "Authorization": "Bearer k1",
                            "X-Service-Identity": "auth_service",
                        },
                    )
                finally:
                    _auth._introspect_client = _pool
        finally:
            app.dependency_overrides.clear()
            get_settings.cache_clear()

        assert r.status_code == 401
        assert r.json()["error_code"] == "INVALID_SERVICE_KEY"

    def test_missing_identity_with_per_service_keys_rejected(self, monkeypatch, db):
        """In per-service mode, omitting X-Service-Identity → 401."""
        from fastapi.testclient import TestClient
        from src.core.config import get_settings
        from src.dependencies.db import get_db
        from src.main import app

        monkeypatch.setenv(
            "SERVICE_API_KEYS",
            json.dumps({"server_service": "k1"}),
        )
        get_settings.cache_clear()

        def _override_db():
            try:
                yield db
            finally:
                pass

        app.dependency_overrides[get_db] = _override_db
        try:
            with TestClient(app) as c:
                from src.dependencies import auth as _auth
                _pool = _auth._introspect_client
                _auth._introspect_client = None
                try:
                    r = c.post(
                        "/api/logging/v1/events",
                        json=make_event(service="server_service"),
                        headers={"Authorization": "Bearer k1"},
                    )
                finally:
                    _auth._introspect_client = _pool
        finally:
            app.dependency_overrides.clear()
            get_settings.cache_clear()

        assert r.status_code == 401
        assert r.json()["error_code"] == "MISSING_SERVICE_IDENTITY"

    def test_legacy_shared_key_payload_rejected(self, client):
        """Legacy single-key режим выкинут: одиночный `Authorization: Bearer`
        без `X-Service-Identity` header'а → 401 `MISSING_SERVICE_IDENTITY`.

        Регрессия-гард: восстановление shared-key fallback'а (`SERVICE_API_KEY`)
        не должно тихо проехать незамеченным.
        """
        # `client` фикстура заранее выставила `SERVICE_API_KEYS` map, но не
        # `SERVICE_API_KEY` — даже валидный bearer без identity header'а
        # должен отбиваться.
        from tests.conftest import TEST_API_KEY

        r = client.post(
            "/api/logging/v1/events",
            json=make_event(),
            headers={"Authorization": f"Bearer {TEST_API_KEY}"},
        )
        assert r.status_code == 401
        assert r.json()["error_code"] == "MISSING_SERVICE_IDENTITY"
