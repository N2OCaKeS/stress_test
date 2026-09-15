"""Тесты hardening-батча.

Покрывает 7 правок:
  * `EventDefinition.default_severity` whitelist через `Literal`.
  * charset валидаторы для `service`/`action`/`username` против CRLF-инъекций.
  * retention-loop advisory-lock + sleep-until для drift.
  * `EventCreate.status` принимает `warning` (используется в server_service).
  * `RESERVED_SERVICE_NAMES` живёт в `core/constants.py`.
  * `upsert_events` использует `pg_insert.on_conflict_do_update`.
  * endpoint'ы рейзят `AppException`-подклассы вместо `HTTPException`.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError
from sqlalchemy import text

from src.core.constants import RESERVED_SERVICE_NAMES
from src.schemas.events import EventCreate
from src.schemas.services import EventDefinition


_BASE_EVENT = {
    "timestamp": datetime.now(timezone.utc),
    "service": "auth_service",
    "action": "user.login",
    "status": "success",
    "allowed": True,
}


# ── EventDefinition.default_severity ────────────────────────────────────────


class TestEventDefinitionDefaultSeverityWhitelist:
    @pytest.mark.parametrize(
        "sev", ["TRACE", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
    )
    def test_whitelisted_value(self, sev: str):
        m = EventDefinition(action="user.login", default_severity=sev)
        assert m.default_severity == sev

    def test_none_allowed(self):
        m = EventDefinition(action="user.login", default_severity=None)
        assert m.default_severity is None

    def test_omitted_defaults_to_none(self):
        m = EventDefinition(action="user.login")
        assert m.default_severity is None

    @pytest.mark.parametrize(
        "bad", ["ROFL", "info", "trace", "WARN", "CRITIC", "1", ""],
    )
    def test_outside_whitelist_rejected(self, bad: str):
        with pytest.raises(ValidationError):
            EventDefinition(action="user.login", default_severity=bad)


# ── EventCreate charset validators ──────────────────────────────────────────


class TestEventCreateServiceCharset:
    @pytest.mark.parametrize(
        "svc", ["auth_service", "x", "abc_xyz", "s" * 64],
    )
    def test_valid_service_accepted(self, svc: str):
        m = EventCreate(**{**_BASE_EVENT, "service": svc})
        assert m.service == svc

    @pytest.mark.parametrize(
        "bad",
        [
            "evil\r\n[ALERT] hijacked",
            "abc def",
            "abc.def",
            "abc-def",
            "abc/def",
            "abc1",
            "ABC",
            "auth_service\x00",
            "",
        ],
    )
    def test_dangerous_service_rejected(self, bad: str):
        with pytest.raises(ValidationError):
            EventCreate(**{**_BASE_EVENT, "service": bad})


class TestEventCreateActionCharset:
    @pytest.mark.parametrize(
        "act",
        [
            "user.login",
            "user",
            "x.y.z",
            "a" * 128,
            "user_logout",
            "user.login.1",
            "provision_v2",
            "http.4xx_error",
        ],
    )
    def test_valid_action_accepted(self, act: str):
        m = EventCreate(**{**_BASE_EVENT, "action": act})
        assert m.action == act

    @pytest.mark.parametrize(
        "bad",
        [
            "user.login\r\n[ALERT] fake",
            "user.LOGIN",
            "user login",
            "user-login",
            "user.login\x00",
            "юзер.логин",
            "",
        ],
    )
    def test_dangerous_action_rejected(self, bad: str):
        with pytest.raises(ValidationError):
            EventCreate(**{**_BASE_EVENT, "action": bad})


class TestEventCreateUsernameCharset:
    """`username` — человекочитаемое имя actor'а (ФИО, логин), не opaque-id.

    Charset-whitelist здесь неуместен (отсёк бы кириллицу/ФИО), поэтому
    валидатор вырезает только control-байты (CR/LF/NUL) — ровно та же
    угроза и то же решение, что у `department_name`.
    """

    @pytest.mark.parametrize(
        "uname",
        [
            "ivanov",
            "Ivan_Ivanov",
            "user@example.com",
            "first.last",
            "abc-123",
            "x" * 128,
            "Анна",
            "Иванов Иван Иванович",
            "юзер",
            "user;DROP TABLE",
            "user<script>",
        ],
    )
    def test_valid_username_accepted(self, uname: str):
        m = EventCreate(**_BASE_EVENT, username=uname)
        assert m.username == uname

    def test_none_username_accepted(self):
        m = EventCreate(**_BASE_EVENT, username=None)
        assert m.username is None

    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("evil\r\n[ALERT] hijacked", "evil[ALERT] hijacked"),
            ("user\x00name", "username"),
        ],
    )
    def test_control_chars_scrubbed(self, raw: str, expected: str):
        m = EventCreate(**_BASE_EVENT, username=raw)
        assert m.username == expected

    def test_control_only_username_becomes_none(self):
        m = EventCreate(**_BASE_EVENT, username="\r\n\x00")
        assert m.username is None

    def test_too_long_username_rejected(self):
        with pytest.raises(ValidationError):
            EventCreate(**_BASE_EVENT, username="x" * 129)


# ── status accepts "warning" ────────────────────────────────────────────────


class TestEventCreateStatusWarning:
    """server_service::internal_service эмитит `status="warning"` в soft mode.

    До правки Pydantic-валидатор резал такие events 422 и они роняли
    audit-emit-цепочку.
    """

    def test_warning_accepted(self):
        m = EventCreate(**{**_BASE_EVENT, "status": "warning"})
        assert m.status == "warning"

    @pytest.mark.parametrize("st", ["success", "failure", "denied", "warning"])
    def test_full_whitelist(self, st: str):
        m = EventCreate(**{**_BASE_EVENT, "status": st})
        assert m.status == st

    @pytest.mark.parametrize("bad", ["warn", "warnings", "Warning", "WARNING"])
    def test_warning_variants_rejected(self, bad: str):
        with pytest.raises(ValidationError):
            EventCreate(**{**_BASE_EVENT, "status": bad})


# ── RESERVED_SERVICE_NAMES в core/constants ─────────────────────────────────


class TestReservedServiceNamesCentralised:
    def test_constant_contains_loging_service(self):
        assert "loging_service" in RESERVED_SERVICE_NAMES

    def test_constant_is_frozenset(self):
        assert isinstance(RESERVED_SERVICE_NAMES, frozenset)

    def test_endpoints_share_same_constant(self):
        """`events.py` и `services.py` импортируют один и тот же frozenset —
        не дублируют локальную копию."""
        from src.api.v1.endpoints import events as ev_mod
        from src.api.v1.endpoints import services as svc_mod

        assert ev_mod.RESERVED_SERVICE_NAMES is RESERVED_SERVICE_NAMES
        assert svc_mod.RESERVED_SERVICE_NAMES is RESERVED_SERVICE_NAMES


# ── upsert idempotent через ON CONFLICT ─────────────────────────────────────


class TestUpsertEventsOnConflict:
    def test_re_upsert_updates_in_place(self, db, TestSessionLocal):
        """Идемпотентный upsert: вторая регистрация апдейтит, не падает."""
        from src.repositories import service_events as se_repo

        s = TestSessionLocal()
        try:
            added1, updated1 = se_repo.upsert_events(
                s, "auth_service",
                [{"action": "user.login", "description": "v1"}],
            )
            assert (added1, updated1) == (1, 0)
            added2, updated2 = se_repo.upsert_events(
                s, "auth_service",
                [{"action": "user.login", "description": "v2",
                  "default_severity": "ERROR"}],
            )
            assert (added2, updated2) == (0, 1)
            rows, _ = se_repo.list_for_service(s, "auth_service")
            assert len(rows) == 1
            assert rows[0].description == "v2"
            assert rows[0].default_severity == "ERROR"
        finally:
            s.close()

    def test_bulk_mixed_added_updated_counts(self, db, TestSessionLocal):
        """В одном батче — часть новых, часть существующих → корректные counts."""
        from src.repositories import service_events as se_repo

        s = TestSessionLocal()
        try:
            se_repo.upsert_events(
                s, "auth_service",
                [{"action": "user.login", "description": "exists"}],
            )
            added, updated = se_repo.upsert_events(
                s, "auth_service",
                [
                    {"action": "user.login", "description": "now-updated"},
                    {"action": "user.logout", "description": "fresh"},
                ],
            )
            assert added == 1
            assert updated == 1
        finally:
            s.close()


# ── AppException-envelope на endpoints ──────────────────────────────────────


def _failing_token_resp(monkeypatch, status_code: int = 401):
    """Подставляет fake httpx.post, возвращающий заданный status."""
    class _FakeResp:
        def __init__(self, status_code: int):
            self.status_code = status_code
            self._json = {}

        def json(self):
            return self._json

    def _fake_post(url, data=None, json=None, headers=None, timeout=None):
        return _FakeResp(status_code)

    from src.api.v1.endpoints import auth as auth_endpoint
    monkeypatch.setattr(auth_endpoint.httpx, "post", _fake_post)


class TestEnvelopeFromAppException:
    def test_404_rule_has_envelope_fields(self, admin_client):
        r = admin_client.get("/api/logging/v1/rules/rule_does_not_exist")
        assert r.status_code == 404
        body = r.json()
        # AppException-envelope schema (vs HTTPException's bare `{detail: ...}`).
        for key in ("error", "error_code", "message", "details", "request_id", "timestamp"):
            assert key in body, f"missing {key} in {body!r}"
        assert body["error_code"] == "RULE_NOT_FOUND"

    def test_409_rule_conflict_envelope(self, admin_client):
        from tests.conftest import make_rule

        admin_client.post("/api/logging/v1/rules", json=make_rule(name="alpha"))
        r = admin_client.post("/api/logging/v1/rules", json=make_rule(name="alpha"))
        assert r.status_code == 409
        body = r.json()
        assert body["error_code"] == "RULE_NAME_CONFLICT"
        assert "alpha" in body["message"]

    def test_422_unknown_match_action_envelope(self, client, admin_client, auth_headers):
        from tests.conftest import make_event_def, make_rule

        client.post(
            "/api/logging/v1/services/auth_service/events",
            json={"events": [make_event_def(action="user.login")]},
            headers=auth_headers,
        )
        r = admin_client.post("/api/logging/v1/rules", json=make_rule(
            name="bad-rule",
            effect="SUPPRESS",
            match_action="user.unknown",
        ))
        assert r.status_code == 422
        body = r.json()
        assert body["error_code"] == "UNKNOWN_MATCH_ACTION"
        assert "not registered" in body["message"].lower()

    def test_token_proxy_invalid_credentials_envelope(self, client, mock_token_proxy):
        # `/token` использует pooled `_token_proxy_client` — подменяем его
        # на MockTransport через фикстуру `mock_token_proxy`. AUTH_SERVICE_URL
        # уже выставлен в фикстуре `client`.
        with mock_token_proxy(status_code=401):
            r = client.post(
                "/api/logging/v1/token",
                data={"username": "x", "password": "y"},
            )
        assert r.status_code == 401
        body = r.json()
        assert body["error_code"] == "INVALID_CREDENTIALS"
        assert "error" in body and "message" in body


# ── retention drift / advisory lock ─────────────────────────────────────────


class TestRetentionAdvisoryLock:
    """`_retention_loop` вызывает `pg_try_advisory_lock` под одним ключом
    из всех replica'ов — только один instance делает cleanup за tick.
    """

    def test_advisory_lock_key_is_stable(self):
        """Ключ — стабильный bigint, чтобы все replica'ы и dba могли проверить
        lock'и в `pg_locks` по одному значению."""
        from src.main import _RETENTION_ADVISORY_LOCK_KEY

        # 8 байт ASCII `loretent` — никаких рандомов.
        expected = int.from_bytes(b"loretent", "big")
        assert _RETENTION_ADVISORY_LOCK_KEY == expected

    def test_advisory_lock_is_acquirable_on_real_db(self, db):
        """Sanity: ключ acquirable в реальной БД и потом отпускается."""
        from src.main import _RETENTION_ADVISORY_LOCK_KEY

        locked = db.execute(
            text("SELECT pg_try_advisory_lock(:k)"),
            {"k": _RETENTION_ADVISORY_LOCK_KEY},
        ).scalar()
        try:
            assert locked is True
            # Второй try из того же connection вернёт True (re-entrant);
            # это ожидаемо для pg_try_advisory_lock на сессии. Проверяем,
            # что unlock проходит.
        finally:
            unlocked = db.execute(
                text("SELECT pg_advisory_unlock(:k)"),
                {"k": _RETENTION_ADVISORY_LOCK_KEY},
            ).scalar()
            db.commit()
            assert unlocked is True
