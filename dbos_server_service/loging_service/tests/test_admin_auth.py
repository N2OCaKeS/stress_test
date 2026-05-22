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
        with patch("src.dependencies.auth.httpx.post") as mock_get:
            mock_get.return_value = MagicMock(status_code=200, json=lambda: {"active": False})
            r = client.get(EVENTS_URL, headers=auth_headers)
        assert r.status_code == 401

    def test_wrong_platform_role_returns_403(self, client):
        """account_admin не может управлять правилами (POST /rules → require_admin требует loging_admin)."""
        with patch("src.dependencies.auth.httpx.post") as mock_get:
            mock_get.return_value = MagicMock(
                status_code=200,
                json=lambda: {"active": True, "subject_type": "user", "sub": "usr_1",
                              "username": "u", "platform_role": "account_admin"},
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
        with patch("src.dependencies.auth.httpx.post",
                   side_effect=httpx.TimeoutException("timeout")):
            r = client.get(EVENTS_URL, headers={"Authorization": "Bearer some-jwt"})
        assert r.status_code == 503
        assert r.json()["error_code"] == "AUTH_SERVICE_TIMEOUT"

    def test_auth_service_unreachable_returns_503(self, client):
        with patch("src.dependencies.auth.httpx.post",
                   side_effect=httpx.ConnectError("refused")):
            r = client.get(EVENTS_URL, headers={"Authorization": "Bearer some-jwt"})
        assert r.status_code == 503
        assert r.json()["error_code"] == "AUTH_SERVICE_UNREACHABLE"

    def test_auth_service_500_returns_503(self, client):
        with patch("src.dependencies.auth.httpx.post") as mock_get:
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

    def test_introspect_call_sends_service_api_key_header(self, client):
        """auth_service /introspect is guarded by require_service_token —
        loging_service must send SERVICE_API_KEY in Authorization header,
        while user's bearer goes into the JSON body."""
        with patch("src.dependencies.auth.httpx.post") as mock_post:
            mock_post.return_value = MagicMock(
                status_code=200,
                json=lambda: {
                    "active": True, "subject_type": "user", "sub": "usr_1",
                    "username": "admin", "platform_role": "loging_admin",
                },
            )
            r = client.get(EVENTS_URL, headers={"Authorization": "Bearer user-jwt-xyz"})

        assert r.status_code == 200
        assert mock_post.called
        call_kwargs = mock_post.call_args.kwargs
        # Service-to-service auth header — exactly the configured SERVICE_API_KEY
        assert call_kwargs["headers"]["Authorization"] == f"Bearer {TEST_API_KEY}"
        # User's token is forwarded only via JSON body, NOT the header
        assert call_kwargs["json"] == {"token": "user-jwt-xyz"}


# ── Разграничение: service token vs admin token ───────────────────────────────

class TestAccessSeparation:
    def test_service_token_can_post_events(self, client, auth_headers):
        assert client.post(EVENTS_URL, headers=auth_headers, json=make_event()).status_code == 201

    def test_service_token_cannot_get_events(self, client, auth_headers):
        with patch("src.dependencies.auth.httpx.post") as mock_get:
            mock_get.return_value = MagicMock(status_code=200, json=lambda: {"active": False})
            assert client.get(EVENTS_URL, headers=auth_headers).status_code == 401

    def test_service_token_cannot_manage_rules(self, client, auth_headers):
        with patch("src.dependencies.auth.httpx.post") as mock_get:
            mock_get.return_value = MagicMock(status_code=200, json=lambda: {"active": False})
            assert client.get(RULES_URL, headers=auth_headers).status_code == 401

    def test_service_token_can_register_service_events(self, client, auth_headers):
        r = client.post(
            f"{SERVICES_URL}/auth_service/events",
            json={"events": [{"action": "user.login"}]},
            headers=auth_headers,
        )
        assert r.status_code == 200

    def test_service_token_cannot_list_services(self, client, auth_headers):
        with patch("src.dependencies.auth.httpx.post") as mock_get:
            mock_get.return_value = MagicMock(status_code=200, json=lambda: {"active": False})
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
            assert r.json()["error_code"] == "AUTH_SERVICE_NOT_CONFIGURED"
        finally:
            get_settings.cache_clear()

    def test_auth_service_unreachable_returns_503(self, client):
        with patch("src.api.v1.endpoints.auth.httpx.post",
                   side_effect=httpx.ConnectError("refused")):
            r = client.post(TOKEN_URL, data={"username": "admin", "password": "x"})
        assert r.status_code == 503
        assert r.json()["error_code"] == "AUTH_SERVICE_UNREACHABLE"

    def test_token_not_in_openapi_schema(self, client):
        paths = client.get("/openapi.json").json().get("paths", {})
        assert "/api/logging/v1/token" not in paths

    # ── Защита от утечки internal hostname через httpx exception repr ──────

    def test_connect_error_does_not_leak_internal_url(self, client):
        """ConnectError несёт ``request.url`` в repr — internal hostname
        не должен попасть в response body (detail / message).
        """
        # httpx.ConnectError с request знает URL → repr включает его
        request = httpx.Request("POST", "http://auth-internal.cluster.local:8000/api/auth/v1/token")
        exc = httpx.ConnectError("Connection refused")
        exc._request = request

        with patch("src.api.v1.endpoints.auth.httpx.post", side_effect=exc):
            r = client.post(TOKEN_URL, data={"username": "admin", "password": "x"})

        assert r.status_code == 503
        body = r.json()
        # Generic envelope: AppException через @app.exception_handler
        assert body["error_code"] == "AUTH_SERVICE_UNREACHABLE"
        assert body["error"] == "service_unavailable"
        assert body["message"] == "Authentication service is temporarily unavailable"
        # ── ключевая инвариант-проверка ───────────────────────────────────
        # Тело ответа не содержит internal hostname / URL / repr исключения.
        # (Порт `:8000` не проверяем как bare-substring — request_id-hex
        #  иногда содержит "8000"; проверяем как часть URL.)
        rendered = r.text.lower()
        assert "auth-internal" not in rendered
        assert "cluster.local" not in rendered
        assert ":8000/" not in rendered
        assert "connection refused" not in rendered

    def test_read_error_does_not_leak_internal_url(self, client):
        """`httpx.ReadError` (другой подкласс TransportError) — тот же инвариант."""
        request = httpx.Request("POST", "http://auth-internal.svc.local/api/auth/v1/token")
        exc = httpx.ReadError("Read timed out")
        exc._request = request

        with patch("src.api.v1.endpoints.auth.httpx.post", side_effect=exc):
            r = client.post(TOKEN_URL, data={"username": "admin", "password": "x"})

        assert r.status_code == 503
        assert r.json()["error_code"] == "AUTH_SERVICE_UNREACHABLE"
        rendered = r.text.lower()
        assert "auth-internal" not in rendered
        assert "svc.local" not in rendered
        assert "read timed out" not in rendered

    def test_generic_exception_does_not_leak_repr(self, client):
        """`except Exception` ловит любое — generic envelope без сырого str(exc)."""
        with patch(
            "src.api.v1.endpoints.auth.httpx.post",
            side_effect=RuntimeError("internal-db-host=secret.internal.example.com"),
        ):
            r = client.post(TOKEN_URL, data={"username": "admin", "password": "x"})

        assert r.status_code == 503
        body = r.json()
        assert body["error_code"] == "AUTH_SERVICE_UNREACHABLE"
        assert "secret.internal.example.com" not in r.text
        assert "internal-db-host" not in r.text

    def test_connect_error_logs_full_detail(self, client, caplog):
        """Полная ошибка (с URL) должна попасть в logs — нужно для on-call."""
        request = httpx.Request("POST", "http://auth-internal.cluster.local:8000/x")
        exc = httpx.ConnectError("Connection refused")
        exc._request = request

        with caplog.at_level("ERROR", logger="src.api.v1.endpoints.auth"):
            with patch("src.api.v1.endpoints.auth.httpx.post", side_effect=exc):
                client.post(TOKEN_URL, data={"username": "admin", "password": "x"})

        # В лог должно быть записано имя сообщения + сам exc (с repr-URL)
        msgs = " ".join(rec.getMessage() for rec in caplog.records)
        assert "auth_service /token proxy failed" in msgs
