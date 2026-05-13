"""Тесты: аутентификация администратора loging_service.

AUTH_SERVICE_URL выставляется в client-фикстуре (conftest.py),
поэтому все тесты, использующие client, автоматически имеют его.
"""

import pytest
from unittest.mock import patch, MagicMock

import httpx

from tests.conftest import TEST_API_KEY, make_event

EVENTS_URL = "/api/logging/v1/events"
RULES_URL = "/api/logging/v1/rules"
SERVICES_URL = "/api/logging/v1/services"
TOKEN_URL = "/api/logging/v1/token"


# ── require_admin ──────────────────────────────────────────────────────────────

class TestRequireAdmin:
    def test_no_token_returns_401(self, client):
        # Нет заголовка Authorization → credentials is None → 401
        r = client.get(EVENTS_URL)
        assert r.status_code == 401

    def test_service_token_rejected(self, client, auth_headers):
        """SERVICE_API_KEY — не JWT. auth_service возвращает 401 → 401."""
        with patch("src.dependencies.auth.httpx.get") as mock_get:
            mock_get.return_value = MagicMock(status_code=401)
            r = client.get(EVENTS_URL, headers=auth_headers)
        assert r.status_code == 401

    def test_wrong_platform_role_returns_403(self, client):
        """account_admin не может управлять правилами (POST /rules → require_admin требует loging_admin)."""
        with patch("src.dependencies.auth.httpx.get") as mock_get:
            mock_get.return_value = MagicMock(
                status_code=200,
                json=lambda: {"user_id": "usr_1", "username": "u", "platform_role": "account_admin"},
            )
            r = client.post(
                RULES_URL,
                headers={"Authorization": "Bearer some-jwt"},
                json={"name": "x", "effect": "SUPPRESS", "priority": 100},
            )
        assert r.status_code == 403
        assert r.json()["error_code"] == "INSUFFICIENT_ROLE"

    def test_loging_admin_role_grants_access(self, admin_client):
        """admin_client использует dependency override — всегда 200."""
        assert admin_client.get(EVENTS_URL).status_code == 200

    def test_auth_service_timeout_returns_503(self, client):
        with patch("src.dependencies.auth.httpx.get",
                   side_effect=httpx.TimeoutException("timeout")):
            r = client.get(EVENTS_URL, headers={"Authorization": "Bearer some-jwt"})
        assert r.status_code == 503
        assert r.json()["error_code"] == "AUTH_SERVICE_TIMEOUT"

    def test_auth_service_unreachable_returns_503(self, client):
        with patch("src.dependencies.auth.httpx.get",
                   side_effect=httpx.ConnectError("refused")):
            r = client.get(EVENTS_URL, headers={"Authorization": "Bearer some-jwt"})
        assert r.status_code == 503
        assert r.json()["error_code"] == "AUTH_SERVICE_UNREACHABLE"

    def test_auth_service_500_returns_503(self, client):
        with patch("src.dependencies.auth.httpx.get") as mock_get:
            mock_get.return_value = MagicMock(status_code=500)
            r = client.get(EVENTS_URL, headers={"Authorization": "Bearer some-jwt"})
        assert r.status_code == 503

    def test_auth_service_not_configured_returns_503(self, client, monkeypatch):
        monkeypatch.setenv("AUTH_SERVICE_URL", "")
        from src.core.config import get_settings
        get_settings.cache_clear()
        try:
            r = client.get(EVENTS_URL, headers={"Authorization": "Bearer some-jwt"})
            assert r.status_code == 503
            assert r.json()["error_code"] == "AUTH_SERVICE_NOT_CONFIGURED"
        finally:
            get_settings.cache_clear()


# ── Разграничение: service token vs admin token ───────────────────────────────

class TestAccessSeparation:
    def test_service_token_can_post_events(self, client, auth_headers):
        assert client.post(EVENTS_URL, headers=auth_headers, json=make_event()).status_code == 201

    def test_service_token_cannot_get_events(self, client, auth_headers):
        with patch("src.dependencies.auth.httpx.get") as mock_get:
            mock_get.return_value = MagicMock(status_code=401)
            assert client.get(EVENTS_URL, headers=auth_headers).status_code == 401

    def test_service_token_cannot_manage_rules(self, client, auth_headers):
        with patch("src.dependencies.auth.httpx.get") as mock_get:
            mock_get.return_value = MagicMock(status_code=401)
            assert client.get(RULES_URL, headers=auth_headers).status_code == 401

    def test_service_token_can_register_service_events(self, client, auth_headers):
        r = client.post(
            f"{SERVICES_URL}/auth_service/events",
            json={"events": [{"action": "user.login"}]},
            headers=auth_headers,
        )
        assert r.status_code == 200

    def test_service_token_cannot_list_services(self, client, auth_headers):
        with patch("src.dependencies.auth.httpx.get") as mock_get:
            mock_get.return_value = MagicMock(status_code=401)
            assert client.get(SERVICES_URL, headers=auth_headers).status_code == 401

    def test_admin_client_can_access_all_admin_endpoints(self, admin_client):
        assert admin_client.get(EVENTS_URL).status_code == 200
        assert admin_client.get(RULES_URL).status_code == 200
        assert admin_client.get(SERVICES_URL).status_code == 200


# ── /token — OAuth2 password flow ─────────────────────────────────────────────

class TestTokenEndpoint:
    def test_valid_credentials_return_access_token(self, client):
        with patch("src.api.v1.endpoints.auth.httpx.post") as mock_post:
            mock_post.return_value = MagicMock(
                status_code=200,
                json=lambda: {"access_token": "jwt-token-123", "token_type": "Bearer"},
            )
            r = client.post(TOKEN_URL, data={"username": "admin", "password": "secret"})
        assert r.status_code == 200
        assert r.json()["access_token"] == "jwt-token-123"
        assert r.json()["token_type"] == "bearer"

    def test_invalid_credentials_return_401(self, client):
        with patch("src.api.v1.endpoints.auth.httpx.post") as mock_post:
            mock_post.return_value = MagicMock(status_code=401)
            r = client.post(TOKEN_URL, data={"username": "admin", "password": "wrong"})
        assert r.status_code == 401

    def test_auth_service_not_configured_returns_503(self, client, monkeypatch):
        monkeypatch.setenv("AUTH_SERVICE_URL", "")
        from src.core.config import get_settings
        get_settings.cache_clear()
        try:
            r = client.post(TOKEN_URL, data={"username": "admin", "password": "x"})
            assert r.status_code == 503
        finally:
            get_settings.cache_clear()

    def test_auth_service_unreachable_returns_503(self, client):
        with patch("src.api.v1.endpoints.auth.httpx.post",
                   side_effect=httpx.ConnectError("refused")):
            r = client.post(TOKEN_URL, data={"username": "admin", "password": "x"})
        assert r.status_code == 503

    def test_token_not_in_openapi_schema(self, client):
        paths = client.get("/openapi.json").json().get("paths", {})
        assert "/api/logging/v1/token" not in paths
