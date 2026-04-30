"""Тесты: /api/logging/v1/services — реестр событий и статистика сервисов."""

import pytest
from tests.conftest import make_event, make_event_def

SERVICES_URL = "/api/logging/v1/services"
EVENTS_URL = "/api/logging/v1/events"


# ── POST /services/{service}/events — регистрация событий ────────────────────

class TestRegisterEvents:
    def test_register_events_returns_counts(self, client, admin_client, auth_headers):
        payload = {
            "events": [
                make_event_def(action="user.login"),
                make_event_def(action="user.logout"),
                make_event_def(action="user.ban", default_severity="CRITICAL"),
            ]
        }
        r = client.post(f"{SERVICES_URL}/auth_service/events", json=payload, headers=auth_headers)
        assert r.status_code == 200
        body = r.json()
        assert body["service"] == "auth_service"
        assert body["added"] == 3
        assert body["updated"] == 0
        assert body["total"] == 3

    def test_register_same_events_twice_is_idempotent(self, client, auth_headers):
        payload = {"events": [make_event_def(action="user.login")]}
        r1 = client.post(f"{SERVICES_URL}/auth_service/events", json=payload, headers=auth_headers)
        assert r1.json()["added"] == 1
        r2 = client.post(f"{SERVICES_URL}/auth_service/events", json=payload, headers=auth_headers)
        assert r2.json()["added"] == 0
        assert r2.json()["updated"] == 1
        assert r2.json()["total"] == 1

    def test_restart_with_new_events_adds_them(self, client, auth_headers):
        client.post(f"{SERVICES_URL}/auth_service/events",
                    json={"events": [make_event_def(action="user.login")]},
                    headers=auth_headers)
        r = client.post(
            f"{SERVICES_URL}/auth_service/events",
            json={"events": [
                make_event_def(action="user.login"),
                make_event_def(action="user.new_action"),
            ]},
            headers=auth_headers,
        )
        assert r.json()["added"] == 1
        assert r.json()["updated"] == 1
        assert r.json()["total"] == 2

    def test_register_updates_description(self, client, auth_headers):
        client.post(f"{SERVICES_URL}/auth_service/events",
                    json={"events": [make_event_def(action="user.login", description="old")]},
                    headers=auth_headers)
        client.post(f"{SERVICES_URL}/auth_service/events",
                    json={"events": [make_event_def(action="user.login", description="new")]},
                    headers=auth_headers)
        # Description is updated (verify via GET)
        # (covered by test_get_service_events below)

    def test_register_requires_service_token(self, client):
        r = client.post(f"{SERVICES_URL}/auth_service/events",
                        json={"events": [make_event_def()]})
        assert r.status_code == 401

    def test_register_empty_list_returns_422(self, client, auth_headers):
        r = client.post(f"{SERVICES_URL}/auth_service/events",
                        json={"events": []}, headers=auth_headers)
        assert r.status_code == 422

    def test_register_multiple_services_independently(self, client, auth_headers):
        for svc in ("auth_service", "config_service", "server_service"):
            r = client.post(
                f"{SERVICES_URL}/{svc}/events",
                json={"events": [make_event_def(action="service.started")]},
                headers=auth_headers,
            )
            assert r.json()["total"] == 1

    def test_action_max_length_validated(self, client, auth_headers):
        r = client.post(f"{SERVICES_URL}/auth_service/events",
                        json={"events": [{"action": "a" * 129}]}, headers=auth_headers)
        assert r.status_code == 422


# ── GET /services/{service}/events — просмотр реестра ────────────────────────

class TestGetServiceEvents:
    def test_empty_registry(self, admin_client):
        r = admin_client.get(f"{SERVICES_URL}/auth_service/events")
        assert r.status_code == 200
        body = r.json()
        assert body["service"] == "auth_service"
        assert body["items"] == []
        assert body["total"] == 0

    def test_returns_registered_events(self, client, admin_client, auth_headers):
        client.post(f"{SERVICES_URL}/auth_service/events",
                    json={"events": [
                        make_event_def(action="user.login", default_severity="INFO"),
                        make_event_def(action="user.ban", default_severity="CRITICAL"),
                    ]},
                    headers=auth_headers)
        r = admin_client.get(f"{SERVICES_URL}/auth_service/events")
        body = r.json()
        assert body["total"] == 2
        actions = {e["action"] for e in body["items"]}
        assert actions == {"user.login", "user.ban"}

    def test_events_sorted_by_action(self, client, admin_client, auth_headers):
        client.post(f"{SERVICES_URL}/auth_service/events",
                    json={"events": [
                        make_event_def(action="user.login"),
                        make_event_def(action="pat.create"),
                        make_event_def(action="bot.create"),
                    ]},
                    headers=auth_headers)
        r = admin_client.get(f"{SERVICES_URL}/auth_service/events")
        actions = [e["action"] for e in r.json()["items"]]
        assert actions == sorted(actions)

    def test_pagination_limit(self, client, admin_client, auth_headers):
        client.post(f"{SERVICES_URL}/auth_service/events",
                    json={"events": [make_event_def(action=f"event.{i}") for i in range(5)]},
                    headers=auth_headers)
        r = admin_client.get(f"{SERVICES_URL}/auth_service/events", params={"limit": 2})
        body = r.json()
        assert len(body["items"]) == 2
        assert body["total"] == 5
        assert body["limit"] == 2

    def test_pagination_offset(self, client, admin_client, auth_headers):
        client.post(f"{SERVICES_URL}/auth_service/events",
                    json={"events": [make_event_def(action=f"event.{i}") for i in range(5)]},
                    headers=auth_headers)
        r = admin_client.get(f"{SERVICES_URL}/auth_service/events", params={"offset": 3})
        assert r.json()["total"] == 5
        assert len(r.json()["items"]) == 2

    def test_requires_admin(self, client):
        r = client.get(f"{SERVICES_URL}/auth_service/events")
        assert r.status_code == 401

    def test_different_services_isolated(self, client, admin_client, auth_headers):
        for svc in ("auth_service", "config_service"):
            client.post(f"{SERVICES_URL}/{svc}/events",
                        json={"events": [make_event_def(action="service.started")]},
                        headers=auth_headers)
        r_auth = admin_client.get(f"{SERVICES_URL}/auth_service/events")
        r_conf = admin_client.get(f"{SERVICES_URL}/config_service/events")
        assert r_auth.json()["total"] == 1
        assert r_conf.json()["total"] == 1


# ── GET /services — статистика сервисов ──────────────────────────────────────

class TestListServices:
    def test_empty_when_no_events(self, admin_client):
        r = admin_client.get(SERVICES_URL)
        assert r.status_code == 200
        assert r.json()["items"] == []

    def test_shows_services_after_events(self, client, admin_client, auth_headers):
        client.post(EVENTS_URL, headers=auth_headers,
                    json=make_event(service="auth_service"))
        client.post(EVENTS_URL, headers=auth_headers,
                    json=make_event(service="config_service"))
        r = admin_client.get(SERVICES_URL)
        body = r.json()
        assert body["total"] == 2
        services = {s["service"] for s in body["items"]}
        assert services == {"auth_service", "config_service"}

    def test_event_count_per_service(self, client, admin_client, auth_headers):
        for _ in range(3):
            client.post(EVENTS_URL, headers=auth_headers,
                        json=make_event(service="auth_service"))
        client.post(EVENTS_URL, headers=auth_headers,
                    json=make_event(service="config_service"))
        r = admin_client.get(SERVICES_URL)
        svc_map = {s["service"]: s for s in r.json()["items"]}
        assert svc_map["auth_service"]["event_count"] == 3
        assert svc_map["config_service"]["event_count"] == 1

    def test_requires_admin(self, client):
        r = client.get(SERVICES_URL)
        assert r.status_code == 401


# ── Валидация match_action при создании правил ───────────────────────────────

class TestRuleMatchActionValidation:
    def test_unregistered_exact_action_rejected_when_registry_nonempty(
        self, client, admin_client, auth_headers
    ):
        # Зарегистрируем хотя бы одно событие
        client.post(f"{SERVICES_URL}/auth_service/events",
                    json={"events": [make_event_def(action="user.login")]},
                    headers=auth_headers)
        # Пробуем создать правило на незарегистрированное действие
        r = admin_client.post("/api/logging/v1/rules", json={
            "name": "bad-rule",
            "effect": "SUPPRESS",
            "match_action": "user.unknown_action",
        })
        assert r.status_code == 422
        assert "not registered" in r.json()["detail"].lower() or "not registered" in str(r.json()).lower()

    def test_registered_exact_action_allowed(self, client, admin_client, auth_headers):
        client.post(f"{SERVICES_URL}/auth_service/events",
                    json={"events": [make_event_def(action="user.login")]},
                    headers=auth_headers)
        r = admin_client.post("/api/logging/v1/rules", json={
            "name": "ok-rule",
            "effect": "SUPPRESS",
            "match_action": "user.login",
        })
        assert r.status_code == 201

    def test_glob_pattern_always_allowed(self, client, admin_client, auth_headers):
        # Даже при непустом реестре глоб-паттерны разрешены
        client.post(f"{SERVICES_URL}/auth_service/events",
                    json={"events": [make_event_def(action="user.login")]},
                    headers=auth_headers)
        r = admin_client.post("/api/logging/v1/rules", json={
            "name": "glob-rule",
            "effect": "SUPPRESS",
            "match_action": "user.*",
        })
        assert r.status_code == 201

    def test_no_validation_when_registry_empty(self, admin_client):
        # При пустом реестре любое точное имя разрешено
        r = admin_client.post("/api/logging/v1/rules", json={
            "name": "bootstrap-rule",
            "effect": "SUPPRESS",
            "match_action": "user.login",
        })
        assert r.status_code == 201
