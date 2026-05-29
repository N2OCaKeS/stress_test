"""Кросс-сервисные интеграционные тесты: поток аудита auth_service → loging_service.

Тесты проверяют, что:
1. Действия в auth_service порождают корректные события в loging_service.
2. loging_admin может управлять правилами, которые влияют на сохранение событий.
3. auth_service при старте регистрирует список своих событий в loging_service.
4. Аудит-события admin-действий сохраняются даже при активных SUPPRESS-правилах.

Оба сервиса работают как реальные процессы (не TestClient).
"""

import time
from datetime import datetime, timezone

import httpx
import pytest

from tests.integration.conftest import ADMIN_PASSWORD, ADMIN_USERNAME, wait_for_event

RULES_URL = "/api/logging/v1/rules"
SERVICES_URL = "/api/logging/v1/services"


# ── Запуск сервиса ─────────────────────────────────────────────────────────────

class TestServiceStartup:
    def test_service_started_event_emitted(
        self,
        logging_client: httpx.Client,
    ):
        """auth_service при старте отправляет событие service.started."""
        # Startup-задача запускает register_events через `asyncio.to_thread`,
        # потом эмитит `service.started`. Под медленным compose-стартом
        # эта цепочка может не успеть до первого опроса — даём ей больше
        # окна, чем дефолтный 15×0.5.
        event = wait_for_event(
            logging_client,
            action="service.started",
            status="success",
            retries=40,
            delay=0.5,
        )
        assert event["service"] == "auth_service"
        assert event["actor_type"] == "service"

    def test_auth_service_registers_its_events(
        self,
        logging_client: httpx.Client,
    ):
        """auth_service при старте регистрирует список своих событий."""
        r = logging_client.get(f"{SERVICES_URL}/auth_service/events")
        assert r.status_code == 200
        body = r.json()
        assert body["total"] > 0
        actions = {e["action"] for e in body["items"]}
        # Ключевые события должны быть зарегистрированы
        assert "user.login" in actions
        assert "service.started" in actions
        assert "pat.create" in actions
        assert "user.ban" in actions


# ── Аутентификация ─────────────────────────────────────────────────────────────

class TestLoginAudit:
    def test_successful_login_produces_info_event(
        self,
        auth_client: httpx.Client,
        logging_client: httpx.Client,
        admin_token: str,
    ):
        event = wait_for_event(
            logging_client,
            action="user.login",
            status="success",
        )
        assert event["service"] == "auth_service"
        assert event["severity"] == "INFO"
        assert event["allowed"] is True

    def test_failed_login_produces_critical_event(
        self,
        auth_client: httpx.Client,
        logging_client: httpx.Client,
    ):
        auth_client.post(
            "/api/auth/v1/login",
            json={"username": "nonexistent_xyz", "password": "wrongpassword"},
        )
        event = wait_for_event(
            logging_client,
            action="user.login",
            status="failure",
        )
        assert event["severity"] == "CRITICAL"
        assert event["service"] == "auth_service"
        assert event["allowed"] is False


# ── Неавторизованный доступ ────────────────────────────────────────────────────

class TestUnauthorizedAccessAudit:
    def test_missing_token_produces_denied_event(
        self,
        auth_client: httpx.Client,
        logging_client: httpx.Client,
    ):
        since = datetime.now(timezone.utc)
        r = auth_client.get("/api/auth/v1/me")
        assert r.status_code == 401

        event = wait_for_event(
            logging_client,
            action="http.access_denied",
            status="denied",
            from_time=since,
        )
        assert event["severity"] == "CRITICAL"
        assert event["service"] == "auth_service"
        assert event["allowed"] is False
        assert event["details"]["status_code"] == 401

    def test_invalid_token_produces_denied_event(
        self,
        auth_client: httpx.Client,
        logging_client: httpx.Client,
    ):
        since = datetime.now(timezone.utc)
        r = auth_client.get(
            "/api/auth/v1/me",
            headers={"Authorization": "Bearer totally.invalid.token"},
        )
        assert r.status_code == 401

        event = wait_for_event(
            logging_client,
            action="http.access_denied",
            status="denied",
            severity="CRITICAL",
            from_time=since,
        )
        assert event["allowed"] is False


# ── Токены ─────────────────────────────────────────────────────────────────────

class TestTokenAudit:
    def test_create_pat_produces_info_event(
        self,
        auth_client: httpx.Client,
        logging_client: httpx.Client,
        admin_token: str,
    ):
        import uuid
        since = datetime.now(timezone.utc)
        # Имя PAT уникально per-user, integration stack не пересоздаёт БД —
        # делаем имя уникальным, чтобы повторный прогон не падал на 409.
        r = auth_client.post(
            "/api/auth/v1/tokens",
            json={
                "name": f"integration-test-pat-{uuid.uuid4().hex[:8]}",
                "allowed_services": ["auth_service"],
            },
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert r.status_code == 201

        event = wait_for_event(
            logging_client,
            action="pat.create",
            status="success",
            severity="INFO",
            from_time=since,
        )
        assert event["service"] == "auth_service"

    def test_list_pats_produces_info_event(
        self,
        auth_client: httpx.Client,
        logging_client: httpx.Client,
        admin_token: str,
    ):
        since = datetime.now(timezone.utc)
        auth_client.get(
            "/api/auth/v1/tokens",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        event = wait_for_event(
            logging_client, action="pat.list", status="success", from_time=since
        )
        assert event["severity"] == "INFO"
        assert event["service"] == "auth_service"


# ── HTTP-ошибки ────────────────────────────────────────────────────────────────

class TestClientErrorAudit:
    def test_404_produces_warning_event(
        self,
        auth_client: httpx.Client,
        logging_client: httpx.Client,
        admin_token: str,
    ):
        since = datetime.now(timezone.utc)
        # `ban_type=permanent` валиден по схеме → доходит до user-lookup,
        # который возвращает 404 USER_NOT_FOUND. Любой невалидный ban_type
        # отлавливался бы pydantic-валидатором и давал 422 раньше handler'а.
        r = auth_client.post(
            "/api/auth/v1/users/usr_nonexistent/ban",
            json={"ban_type": "permanent"},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert r.status_code == 404

        event = wait_for_event(
            logging_client,
            action="http.client_error",
            status="failure",
            from_time=since,
        )
        assert event["severity"] == "WARNING"
        assert event["details"]["status_code"] == 404

    def test_422_produces_warning_event(
        self,
        auth_client: httpx.Client,
        logging_client: httpx.Client,
        admin_token: str,
    ):
        since = datetime.now(timezone.utc)
        r = auth_client.post(
            "/api/auth/v1/users",
            json={"username": "x"},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert r.status_code == 422

        event = wait_for_event(
            logging_client,
            action="http.client_error",
            status="failure",
            from_time=since,
        )
        assert event["severity"] == "WARNING"
        assert event["details"]["status_code"] == 422


# ── /me ────────────────────────────────────────────────────────────────────────

class TestMeAudit:
    def test_me_produces_info_event(
        self,
        auth_client: httpx.Client,
        logging_client: httpx.Client,
        admin_token: str,
    ):
        since = datetime.now(timezone.utc)
        r = auth_client.get(
            "/api/auth/v1/me",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert r.status_code == 200

        event = wait_for_event(
            logging_client, action="user.me", status="success", from_time=since
        )
        assert event["service"] == "auth_service"
        assert event["severity"] == "INFO"
        assert event["actor_id"] is not None


# ── loging_admin управление правилами ─────────────────────────────────────────

class TestLoggingAdminWorkflow:
    def test_loging_admin_can_list_events(
        self,
        logging_client: httpx.Client,
    ):
        r = logging_client.get("/api/logging/v1/events")
        assert r.status_code == 200

    def test_loging_admin_can_create_and_apply_rule(
        self,
        auth_client: httpx.Client,
        logging_client: httpx.Client,
        admin_token: str,
    ):
        since = datetime.now(timezone.utc)
        # Правило: повысить severity успешного user.login c INFO до WARNING
        r = logging_client.post(RULES_URL, json={
            "name": "escalate-login-success-integration",
            "effect": "OVERRIDE_SEVERITY",
            "match_action": "user.login",
            "match_status": "success",
            "effect_severity": "WARNING",
            "priority": 500,
        })
        assert r.status_code == 201
        rule_id = r.json()["id"]

        try:
            auth_client.post(
                "/api/auth/v1/login",
                json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD},
            )
            event = wait_for_event(
                logging_client,
                action="user.login",
                status="success",
                severity="WARNING",
                from_time=since,
            )
            assert event["service"] == "auth_service"
        finally:
            logging_client.delete(f"{RULES_URL}/{rule_id}")

    def test_rule_create_produces_warning_audit(
        self,
        logging_client: httpx.Client,
    ):
        since = datetime.now(timezone.utc)
        r = logging_client.post(RULES_URL, json={
            "name": "audit-test-rule",
            "effect": "ALLOW",
            "priority": 1,
        })
        assert r.status_code == 201
        rule_id = r.json()["id"]
        logging_client.delete(f"{RULES_URL}/{rule_id}")

        event = wait_for_event(
            logging_client,
            action="logging_rule.create",
            status="success",
            from_time=since,
        )
        assert event["service"] == "loging_service"
        assert event["severity"] == "WARNING"

    def test_rule_delete_produces_critical_audit(
        self,
        logging_client: httpx.Client,
    ):
        since = datetime.now(timezone.utc)
        r = logging_client.post(RULES_URL, json={
            "name": "to-delete-for-audit",
            "effect": "SUPPRESS",
            "priority": 1,
        })
        rule_id = r.json()["id"]
        logging_client.delete(f"{RULES_URL}/{rule_id}")

        event = wait_for_event(
            logging_client,
            action="logging_rule.delete",
            status="success",
            from_time=since,
        )
        assert event["severity"] == "CRITICAL"
        assert event["details"]["rule_id"] == rule_id

    def test_admin_audit_bypasses_suppress_rules(
        self,
        logging_client: httpx.Client,
    ):
        """Admin audit events are stored even when a SUPPRESS rule targets loging_service."""
        since = datetime.now(timezone.utc)

        # Создаём правило подавить всё от loging_service
        suppress_r = logging_client.post(RULES_URL, json={
            "name": "suppress-loging-service-integ",
            "effect": "SUPPRESS",
            "match_service": "loging_service",
            "priority": 999,
        })
        suppress_id = suppress_r.json()["id"]

        try:
            # Создаём ещё одно правило — аудит должен записаться несмотря на SUPPRESS
            r = logging_client.post(RULES_URL, json={
                "name": "should-still-audit",
                "effect": "ALLOW",
                "priority": 1,
            })
            assert r.status_code == 201
            new_rule_id = r.json()["id"]
            logging_client.delete(f"{RULES_URL}/{new_rule_id}")

            event = wait_for_event(
                logging_client,
                action="logging_rule.create",
                status="success",
                from_time=since,
            )
            assert event["service"] == "loging_service"
        finally:
            logging_client.delete(f"{RULES_URL}/{suppress_id}")

    def test_non_admin_cannot_access_events(
        self,
        auth_client: httpx.Client,
        admin_token: str,
    ):
        """Обычный пользователь (без loging_*-ролей) не имеет доступа к /events.

        Замечание: account_admin ИМЕЕТ доступ через `require_reader` —
        отдельно проверяется ниже. Тут берём пользователя с department_id и
        без service-роли в loging_service.
        """
        import uuid
        import httpx as _httpx
        from tests.integration.conftest import LOGGING_URL

        # Создаём department + regular user (без loging_*).
        dept_name = f"non_admin_dept_{uuid.uuid4().hex[:8]}"
        dept_resp = auth_client.post(
            "/api/auth/v1/departments",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"name": dept_name, "display_name": dept_name},
        )
        assert dept_resp.status_code == 201
        dept_id = dept_resp.json()["department_id"]

        username = f"regular_user_{uuid.uuid4().hex[:8]}"
        password = "Regular1234!"
        ur = auth_client.post(
            "/api/auth/v1/users",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"username": username, "password": password, "department_id": dept_id},
        )
        assert ur.status_code == 201, ur.text

        login = auth_client.post(
            "/api/auth/v1/login", json={"username": username, "password": password},
        )
        regular_token = login.json()["access_token"]

        with _httpx.Client(base_url=LOGGING_URL, timeout=10) as c:
            r = c.get(
                "/api/logging/v1/events",
                headers={"Authorization": f"Bearer {regular_token}"},
            )
        assert r.status_code == 403
        assert r.json()["error_code"] == "INSUFFICIENT_ROLE"


# ── Сервис списка событий ──────────────────────────────────────────────────────

class TestServiceRegistry:
    def test_auth_service_appears_in_services_list(
        self,
        logging_client: httpx.Client,
    ):
        r = logging_client.get(SERVICES_URL)
        assert r.status_code == 200
        services = {s["service"] for s in r.json()["items"]}
        assert "auth_service" in services

    def test_service_event_count_increases_after_actions(
        self,
        auth_client: httpx.Client,
        logging_client: httpx.Client,
        admin_token: str,
    ):
        before = next(
            (s["event_count"] for s in logging_client.get(SERVICES_URL).json()["items"]
             if s["service"] == "auth_service"),
            0
        )
        # Trigger a few events
        auth_client.get(
            "/api/auth/v1/me",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        time.sleep(1)
        after = next(
            (s["event_count"] for s in logging_client.get(SERVICES_URL).json()["items"]
             if s["service"] == "auth_service"),
            0
        )
        assert after > before
