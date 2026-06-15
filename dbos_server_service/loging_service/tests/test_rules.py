"""Тесты: /api/logging/v1/rules — CRUD правил и их применение к событиям.

Все правила требуют platform_role=loging_admin → используем admin_client.
POST /events (для проверки применения правил) → client + auth_headers.
"""

import pytest
from tests.conftest import make_event, make_rule

RULES_URL = "/api/logging/v1/rules"
EVENTS_URL = "/api/logging/v1/events"


# ── CRUD ──────────────────────────────────────────────────────────────────────

class TestRulesCRUD:
    def test_list_managed_rules_empty(self, admin_client):
        # Managed-правил ещё нет (дефолты не сидируются `db`-фикстурой).
        r = admin_client.get(RULES_URL, params={"is_default": "false"})
        assert r.status_code == 200
        assert r.json()["items"] == []
        assert r.json()["total"] == 0

    def test_list_includes_default_rules(self, admin_client, db):
        # Сеем дефолты и проверяем, что они видны в списке с пометкой.
        from src.services.rule_service import seed_default_rules
        seed_default_rules(db)
        r = admin_client.get(RULES_URL, params={"is_default": "true"})
        assert r.status_code == 200
        body = r.json()
        assert body["total"] > 0
        assert all(item["is_default"] is True for item in body["items"])

    def test_create_rule(self, admin_client):
        r = admin_client.post(RULES_URL, json=make_rule())
        assert r.status_code == 201
        body = r.json()
        assert body["name"] == "test-rule"
        assert body["effect"] == "SUPPRESS"
        assert body["is_active"] is True
        assert body["id"].startswith("rl_")

    def test_get_rule(self, admin_client):
        created = admin_client.post(RULES_URL, json=make_rule(name="get-me")).json()
        r = admin_client.get(f"{RULES_URL}/{created['id']}")
        assert r.status_code == 200
        assert r.json()["id"] == created["id"]

    def test_get_rule_not_found(self, admin_client):
        assert admin_client.get(f"{RULES_URL}/rl_nonexistent").status_code == 404

    def test_update_rule_priority_and_description(self, admin_client):
        created = admin_client.post(RULES_URL, json=make_rule(name="upd-me")).json()
        r = admin_client.patch(
            f"{RULES_URL}/{created['id']}",
            json={"priority": 500, "description": "обновлено"},
        )
        assert r.status_code == 200
        assert r.json()["priority"] == 500
        assert r.json()["description"] == "обновлено"

    def test_update_rule_deactivate(self, admin_client):
        created = admin_client.post(RULES_URL, json=make_rule(name="deact")).json()
        r = admin_client.patch(f"{RULES_URL}/{created['id']}", json={"is_active": False})
        assert r.json()["is_active"] is False

    def test_delete_rule(self, admin_client):
        created = admin_client.post(RULES_URL, json=make_rule(name="del-me")).json()
        assert admin_client.delete(f"{RULES_URL}/{created['id']}").status_code == 204
        assert admin_client.get(f"{RULES_URL}/{created['id']}").status_code == 404

    def test_delete_rule_frees_name_for_recreate(self, admin_client):
        """Soft-delete переименовывает row, чтобы UNIQUE на name не блокировал
        повторное создание правила с тем же именем."""
        created = admin_client.post(RULES_URL, json=make_rule(name="recycle")).json()
        admin_client.delete(f"{RULES_URL}/{created['id']}")
        again = admin_client.post(RULES_URL, json=make_rule(name="recycle"))
        assert again.status_code == 201
        assert again.json()["id"] != created["id"]

    def test_delete_rule_does_not_appear_in_list(self, admin_client):
        """Soft-delete не должен показывать удалённое правило в GET /rules
        (filter по deleted_at IS NULL)."""
        created = admin_client.post(RULES_URL, json=make_rule(name="hide-me")).json()
        admin_client.delete(f"{RULES_URL}/{created['id']}")
        body = admin_client.get(RULES_URL).json()
        ids = [r["id"] for r in body["items"]]
        assert created["id"] not in ids

    def test_duplicate_name_returns_409(self, admin_client):
        admin_client.post(RULES_URL, json=make_rule(name="dup"))
        assert admin_client.post(RULES_URL, json=make_rule(name="dup")).status_code == 409

    def test_override_severity_requires_effect_severity(self, admin_client):
        r = admin_client.post(RULES_URL, json=make_rule(effect="OVERRIDE_SEVERITY"))
        assert r.status_code == 422

    def test_override_severity_with_effect_severity_ok(self, admin_client):
        r = admin_client.post(
            RULES_URL, json=make_rule(effect="OVERRIDE_SEVERITY", effect_severity="CRITICAL")
        )
        assert r.status_code == 201
        assert r.json()["effect_severity"] == "CRITICAL"

    def test_effect_severity_only_for_override(self, admin_client):
        r = admin_client.post(
            RULES_URL, json=make_rule(effect="SUPPRESS", effect_severity="INFO")
        )
        assert r.status_code == 422

    def test_priority_bounds(self, admin_client):
        assert admin_client.post(
            RULES_URL, json=make_rule(name="low", priority=0)
        ).status_code == 422
        assert admin_client.post(
            RULES_URL, json=make_rule(name="high", priority=1001)
        ).status_code == 422
        assert admin_client.post(
            RULES_URL, json=make_rule(name="ok", priority=1000)
        ).status_code == 201

    def test_requires_admin(self, client):
        assert client.get(RULES_URL).status_code == 401
        assert client.post(RULES_URL, json=make_rule()).status_code == 401

    def test_list_returns_pagination(self, admin_client):
        for i in range(5):
            admin_client.post(RULES_URL, json=make_rule(name=f"r{i}"))
        # Фильтруем managed-правила, чтобы засеянные дефолты не сбивали счёт.
        body = admin_client.get(
            RULES_URL, params={"limit": 2, "is_default": "false"}
        ).json()
        assert len(body["items"]) == 2
        assert body["total"] == 5
        assert body["limit"] == 2

    def test_offset_exceeds_max_returns_422(self, admin_client):
        assert admin_client.get(
            RULES_URL, params={"offset": 10_000_001}
        ).status_code == 422


# ── Применение правил ─────────────────────────────────────────────────────────

class TestRuleApplication:
    def test_suppress_prevents_storage(self, client, admin_client, auth_headers):
        admin_client.post(RULES_URL, json=make_rule(
            name="suppress-login", effect="SUPPRESS", match_action="user.login",
        ))
        assert client.post(EVENTS_URL, headers=auth_headers,
                           json=make_event(action="user.login")).status_code == 204
        assert admin_client.get(
            EVENTS_URL, params={"action": "user.login", "include_total": "true"}
        ).json()["total"] == 0

    def test_suppress_does_not_affect_other_actions(self, client, admin_client, auth_headers):
        admin_client.post(RULES_URL, json=make_rule(
            name="suppress-login2", effect="SUPPRESS", match_action="user.login",
        ))
        assert client.post(EVENTS_URL, headers=auth_headers,
                           json=make_event(action="user.logout")).status_code == 201

    def test_override_severity_applied(self, client, admin_client, auth_headers):
        admin_client.post(RULES_URL, json=make_rule(
            name="escalate", effect="OVERRIDE_SEVERITY",
            match_severity="WARNING", effect_severity="CRITICAL",
        ))
        event_id = client.post(EVENTS_URL, headers=auth_headers,
                               json=make_event(severity="WARNING")).json()["id"]
        items = admin_client.get(EVENTS_URL).json()["items"]
        event = next(e for e in items if e["id"] == event_id)
        assert event["severity"] == "CRITICAL"

    def test_allow_stops_chain(self, client, admin_client, auth_headers):
        admin_client.post(RULES_URL, json=make_rule(
            name="allow-high", effect="ALLOW", priority=200, match_action="user.login",
        ))
        admin_client.post(RULES_URL, json=make_rule(
            name="suppress-low", effect="SUPPRESS", priority=100, match_action="user.login",
        ))
        assert client.post(EVENTS_URL, headers=auth_headers,
                           json=make_event(action="user.login")).status_code == 201

    def test_inactive_rule_ignored(self, client, admin_client, auth_headers):
        created = admin_client.post(RULES_URL, json=make_rule(
            name="inactive", effect="SUPPRESS", match_action="user.login",
        )).json()
        admin_client.patch(f"{RULES_URL}/{created['id']}", json={"is_active": False})
        assert client.post(EVENTS_URL, headers=auth_headers,
                           json=make_event(action="user.login")).status_code == 201

    def test_glob_pattern_matches_multiple(self, client, admin_client, auth_headers):
        admin_client.post(RULES_URL, json=make_rule(
            name="suppress-user", effect="SUPPRESS", match_action="user.*",
        ))
        for action in ("user.login", "user.logout", "user.ban"):
            assert client.post(EVENTS_URL, headers=auth_headers,
                               json=make_event(action=action)).status_code == 204
        # glob не пересекает точку
        assert client.post(EVENTS_URL, headers=auth_headers,
                           json=make_event(action="http.access_denied")).status_code == 201

    def test_glob_does_not_cross_dot_boundary(self, client, admin_client, auth_headers):
        """user.* НЕ подавляет user.login.extra."""
        admin_client.post(RULES_URL, json=make_rule(
            name="user-star", effect="SUPPRESS", match_action="user.*",
        ))
        # user.login.extra не совпадает с user.*
        assert client.post(EVENTS_URL, headers=auth_headers,
                           json=make_event(action="user.login.extra")).status_code == 201

    def test_match_service_filter(self, client, admin_client, auth_headers):
        admin_client.post(RULES_URL, json=make_rule(
            name="suppress-config", effect="SUPPRESS", match_service="config_service",
        ))
        # X-Service-Identity подменяется под каждый payload.service —
        # иначе SERVICE_IDENTITY_PAYLOAD_MISMATCH guard режет ingest на 403.
        assert client.post(
            EVENTS_URL,
            headers=auth_headers | {"X-Service-Identity": "auth_service"},
            json=make_event(service="auth_service"),
        ).status_code == 201
        assert client.post(
            EVENTS_URL,
            headers=auth_headers | {"X-Service-Identity": "config_service"},
            json=make_event(service="config_service"),
        ).status_code == 204

    def test_match_status_filter(self, client, admin_client, auth_headers):
        admin_client.post(RULES_URL, json=make_rule(
            name="suppress-success", effect="SUPPRESS", match_status="success",
        ))
        assert client.post(EVENTS_URL, headers=auth_headers,
                           json=make_event(status="success", allowed=True)).status_code == 204
        assert client.post(EVENTS_URL, headers=auth_headers,
                           json=make_event(status="failure", allowed=False)).status_code == 201

    def test_priority_ordering(self, client, admin_client, auth_headers):
        admin_client.post(RULES_URL, json=make_rule(
            name="prio-suppress", effect="SUPPRESS", priority=50, match_action="user.login",
        ))
        admin_client.post(RULES_URL, json=make_rule(
            name="prio-allow", effect="ALLOW", priority=200, match_action="user.login",
        ))
        assert client.post(EVENTS_URL, headers=auth_headers,
                           json=make_event(action="user.login")).status_code == 201

    def test_combined_criteria_all_must_match(self, client, admin_client, auth_headers):
        admin_client.post(RULES_URL, json=make_rule(
            name="combined", effect="SUPPRESS",
            match_service="auth_service", match_action="user.login", match_status="failure",
        ))
        # success не подавляется
        assert client.post(EVENTS_URL, headers=auth_headers,
                           json=make_event(action="user.login", status="success")).status_code == 201
        # failure подавляется
        assert client.post(EVENTS_URL, headers=auth_headers,
                           json=make_event(action="user.login", status="failure", allowed=False)).status_code == 204


# ── Аудит admin-действий (без ротации) ────────────────────────────────────────

class TestAdminAudit:
    def test_rule_create_produces_audit_event(self, admin_client, db):
        """Создание правила создаёт аудит-событие logging_rule.create, минуя правила."""
        admin_client.post(RULES_URL, json=make_rule(name="audited"))
        from src.repositories.events import query as repo_query
        from src.models.audit_event import AuditEvent
        from sqlalchemy import select
        events = db.execute(
            select(AuditEvent).where(AuditEvent.action == "logging_rule.create")
        ).scalars().all()
        assert len(events) == 1
        assert events[0].service == "loging_service"
        assert events[0].actor_id == "usr_test_admin"

    def test_rule_delete_produces_critical_audit_event(self, admin_client, db):
        created = admin_client.post(RULES_URL, json=make_rule(name="to-audit-del")).json()
        admin_client.delete(f"{RULES_URL}/{created['id']}")
        from src.models.audit_event import AuditEvent
        from sqlalchemy import select
        events = db.execute(
            select(AuditEvent).where(AuditEvent.action == "logging_rule.delete")
        ).scalars().all()
        assert len(events) == 1
        assert events[0].severity == "CRITICAL"

    def test_rule_create_audit_carries_username_and_department(self, admin_client, db):
        """`_audit()` пробрасывает username и department_id из identity.

        Без этого SIEM теряет атрибуцию: видит opaque `actor_id`, не имя
        человека и не его отдел. `main.py::audit_access` это делает —
        тут симметрия для admin-действий.
        """
        admin_client.post(RULES_URL, json=make_rule(name="audit-identity-fields"))
        from src.models.audit_event import AuditEvent
        from sqlalchemy import select
        events = db.execute(
            select(AuditEvent).where(AuditEvent.action == "logging_rule.create")
        ).scalars().all()
        assert len(events) == 1
        assert events[0].username == "test_admin"
        # `loging_admin` в ADMIN_IDENTITY фикстуре глобален → department_id=None;
        # важно, что поле явно прокинуто (а не «забыто» в schema-defaults).
        assert events[0].department_id is None

    def test_rule_create_audit_with_scoped_admin_department(self, db, monkeypatch):
        """Если у admin'а есть department_id — он попадает в audit-event."""
        from src.dependencies.auth import (
            require_admin,
            require_reader,
        )
        from src.main import app
        from src.dependencies.db import get_db
        from fastapi.testclient import TestClient

        scoped_identity = {
            "user_id": "usr_dept_admin",
            "username": "dept_admin_user",
            "platform_role": "loging_admin",
            "department_id": "dep_finance",
            "allowed_services": [],
            "service_roles": {},
        }
        monkeypatch.setenv(
            "SERVICE_API_KEYS",
            '{"auth_service":"test-service-api-key"}',
        )
        from src.core.config import get_settings
        get_settings.cache_clear()

        def _override():
            try:
                yield db
            finally:
                pass

        app.dependency_overrides[get_db] = _override
        app.dependency_overrides[require_admin] = lambda: scoped_identity
        app.dependency_overrides[require_reader] = lambda: scoped_identity
        try:
            with TestClient(app) as c:
                c.post(RULES_URL, json=make_rule(name="scoped-audit-rule"))
            from src.models.audit_event import AuditEvent
            from sqlalchemy import select
            events = db.execute(
                select(AuditEvent).where(AuditEvent.action == "logging_rule.create")
            ).scalars().all()
            assert len(events) == 1
            assert events[0].username == "dept_admin_user"
            assert events[0].department_id == "dep_finance"
        finally:
            app.dependency_overrides.clear()
            get_settings.cache_clear()

    def test_admin_audit_bypasses_suppress_rules(self, client, admin_client, auth_headers, db):
        """SUPPRESS на loging_service не подавляет admin-аудит."""
        # Добавляем правило подавить все события loging_service
        admin_client.post(RULES_URL, json=make_rule(
            name="suppress-loging", effect="SUPPRESS", match_service="loging_service",
        ))
        # Создаём ещё одно правило — аудит должен записаться несмотря на SUPPRESS
        admin_client.post(RULES_URL, json=make_rule(name="trigger-audit"))
        from src.models.audit_event import AuditEvent
        from sqlalchemy import select
        admin_events = db.execute(
            select(AuditEvent).where(AuditEvent.service == "loging_service")
        ).scalars().all()
        # Оба create-аудита должны быть записаны
        assert len(admin_events) == 2
