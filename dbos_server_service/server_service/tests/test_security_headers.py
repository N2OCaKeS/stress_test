"""Тесты для SecurityHeadersMiddleware из `src/main.py`.

Проверяем, что общие защитные заголовки (X-Frame-Options,
X-Content-Type-Options, Referrer-Policy, CSP frame-ancestors) попадают на
любой ответ, включая ошибки и health-paths, а HSTS появляется только при
`SECURITY_HSTS_ENABLED=true`.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from tests._helpers import assert_error

BASE = "/api/server/v1"

_ALWAYS_ON = {
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
    "Permissions-Policy": (
        "geolocation=(), microphone=(), camera=(), payment=(), usb=()"
    ),
}


class TestSecurityHeadersAlwaysOn:
    async def test_health_carries_security_headers(self, client):
        resp = await client.get(f"{BASE}/health")
        assert resp.status_code == 200
        for header, value in _ALWAYS_ON.items():
            assert resp.headers.get(header) == value

    async def test_unauthorized_response_carries_security_headers(self, client):
        # 401-ветка (нет Authorization) — заголовки должны быть и на ошибке.
        resp = await client.get(f"{BASE}/servers")
        assert_error(resp, 401, "ACCESS_TOKEN_MISSING")
        for header, value in _ALWAYS_ON.items():
            assert resp.headers.get(header) == value

    async def test_hsts_absent_by_default(self, client):
        # SECURITY_HSTS_ENABLED не выставлен в тестовом env → HSTS не ставится
        # (за http-фронтом он сломал бы клиентов на rebound'е).
        resp = await client.get(f"{BASE}/health")
        assert "Strict-Transport-Security" not in resp.headers


class TestSecurityHeadersHsts:
    @pytest.fixture
    def hsts_app(self, monkeypatch):
        """Свежий FastAPI-app с включённым HSTS.

        Нельзя дёрнуть модульный `app` (он собран на import'е с дефолтными
        settings) — строим новый через `create_application` после patch'а
        env + сброса кэша Settings.
        """
        from src.core.config import get_settings
        from src.main import create_application

        monkeypatch.setenv("SECURITY_HSTS_ENABLED", "true")
        get_settings.cache_clear()  # type: ignore[attr-defined]
        app = create_application()
        try:
            yield app
        finally:
            get_settings.cache_clear()  # type: ignore[attr-defined]

    async def test_hsts_present_when_enabled(self, hsts_app):
        async with AsyncClient(
            transport=ASGITransport(app=hsts_app), base_url="http://test",
        ) as ac:
            resp = await ac.get(f"{BASE}/health")
        assert resp.status_code == 200
        assert (
            resp.headers.get("Strict-Transport-Security")
            == "max-age=63072000; includeSubDomains"
        )
        # Прочие заголовки тоже на месте.
        for header, value in _ALWAYS_ON.items():
            assert resp.headers.get(header) == value
