"""Тесты для HTTPSRequiredMiddleware из `src/middleware/https_guard.py`.

Проверяем:
* В production cleartext-HTTP отбивается 403 ``HTTPS_REQUIRED``.
* В production health/ready проходят без TLS (k8s probe на pod-network http).
* В dev/test middleware пропускает всё (тесты гоняются через ASGI без TLS,
  devcontainer на http).
* X-Forwarded-Proto: https — запрос считается https'ным (за TLS-терминатором).
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from tests._helpers import assert_error

BASE = "/api/server/v1"


@pytest.fixture
def production_app(monkeypatch):
    """Свежий FastAPI-app с ``APP_ENV=production`` и trusted-proxy=127.0.0.1.

    Нельзя дёрнуть модульный `app` — он собран на import'е с дефолтным
    ``APP_ENV``. Строим новый через ``create_application`` после patch'а env
    + сброса кэша ``get_settings``.

    В production включены обязательные guard'ы (HKDF salt, SERVICE_API_KEY,
    https в logging/auth URL'ах) — выставляем минимальный валидный набор,
    чтобы Settings собрался, но не падал на _require_https_* / _validate_*.

    `TRUSTED_PROXY_IPS=127.0.0.1` — ASGITransport-клиент ходит с client
    host 127.0.0.1; чтобы XFP-доверие отрабатывало, добавляем его в
    allow-list. Без этого все XFP-тесты получат 403 (правильно, без
    trusted-proxy XFP не доверяется — отдельный тест ниже).
    """
    from src.core.config import get_settings
    from src.main import create_application

    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("SERVICE_API_KEY", "test-service-api-key-not-used")
    monkeypatch.setenv("LOGGING_SERVICE_URL", "https://logging.invalid")
    monkeypatch.setenv("AUTH_SERVICE_URL", "https://auth.invalid")
    monkeypatch.setenv("SERVER_ENCRYPTION_KEY_VERSION", "2")
    # pydantic-settings v2 для `list[str]`-полей сначала пробует JSON-decode
    # env-значения и только потом отдаёт его в field_validator. Comma-string
    # из env вылетит JSONDecodeError ещё до валидатора, поэтому передаём
    # JSON-форму.
    monkeypatch.setenv("TRUSTED_PROXY_IPS", '["127.0.0.1"]')
    get_settings.cache_clear()  # type: ignore[attr-defined]
    app = create_application()
    try:
        yield app
    finally:
        get_settings.cache_clear()  # type: ignore[attr-defined]


@pytest.fixture
def production_app_no_trusted_proxy(monkeypatch):
    """Тот же production-app, но без TRUSTED_PROXY_IPS.

    Для теста, что XFP игнорируется, когда allow-list прокси пустой.
    """
    from src.core.config import get_settings
    from src.main import create_application

    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("SERVICE_API_KEY", "test-service-api-key-not-used")
    monkeypatch.setenv("LOGGING_SERVICE_URL", "https://logging.invalid")
    monkeypatch.setenv("AUTH_SERVICE_URL", "https://auth.invalid")
    monkeypatch.setenv("SERVER_ENCRYPTION_KEY_VERSION", "2")
    monkeypatch.delenv("TRUSTED_PROXY_IPS", raising=False)
    get_settings.cache_clear()  # type: ignore[attr-defined]
    app = create_application()
    try:
        yield app
    finally:
        get_settings.cache_clear()  # type: ignore[attr-defined]


class TestHttpsGuardProduction:
    async def test_cleartext_http_rejected_in_production(self, production_app):
        async with AsyncClient(
            transport=ASGITransport(app=production_app), base_url="http://test",
        ) as ac:
            resp = await ac.get(f"{BASE}/servers")
        body = assert_error(resp, 403, "HTTPS_REQUIRED")
        assert body["error"] == "forbidden"
        assert "scheme" in body["details"]

    async def test_health_bypasses_https_guard_in_production(self, production_app):
        # k8s probe ходит на pod-network http — не должен валиться 403.
        async with AsyncClient(
            transport=ASGITransport(app=production_app), base_url="http://test",
        ) as ac:
            resp = await ac.get(f"{BASE}/health")
        assert resp.status_code == 200

    async def test_ready_bypasses_https_guard_in_production(self, production_app):
        async with AsyncClient(
            transport=ASGITransport(app=production_app), base_url="http://test",
        ) as ac:
            resp = await ac.get(f"{BASE}/ready")
        # /ready может вернуть 200 или 503 в зависимости от состояния БД,
        # но точно не 403 от HTTPS-guard'а.
        assert resp.status_code != 403

    async def test_x_forwarded_proto_https_passes(self, production_app):
        # За TLS-терминатором (ingress) scheme в ASGI-scope остаётся http,
        # но X-Forwarded-Proto: https говорит, что клиент пришёл по TLS.
        async with AsyncClient(
            transport=ASGITransport(app=production_app), base_url="http://test",
        ) as ac:
            resp = await ac.get(
                f"{BASE}/servers",
                headers={"X-Forwarded-Proto": "https"},
            )
        # 401 (нет Authorization) или другая ошибка от endpoint'а — всё, что
        # не 403 HTTPS_REQUIRED, означает что guard пропустил запрос дальше.
        assert resp.status_code != 403 or resp.json().get("error_code") != "HTTPS_REQUIRED"

    async def test_x_forwarded_proto_multi_hop_trusts_last(self, production_app):
        # XFP за несколькими hop'ами выглядит как "client_value, ingress_value".
        # Доверяем правому-крайнему токену — это closest-trusted-proxy
        # (наш ingress); левее — клиент мог подделать.
        async with AsyncClient(
            transport=ASGITransport(app=production_app), base_url="http://test",
        ) as ac:
            resp = await ac.get(
                f"{BASE}/servers",
                headers={"X-Forwarded-Proto": "http, https"},
            )
        # last="https" → guard пропустил.
        assert resp.status_code != 403 or resp.json().get("error_code") != "HTTPS_REQUIRED"

    async def test_x_forwarded_proto_spoofed_first_token_rejected(self, production_app):
        # Атакующий ставит `X-Forwarded-Proto: https` сам, ingress дописывает
        # `, http` (фактическая схема hop'а). Раньше брали first → пускали;
        # теперь берём last="http" → 403.
        async with AsyncClient(
            transport=ASGITransport(app=production_app), base_url="http://test",
        ) as ac:
            resp = await ac.get(
                f"{BASE}/servers",
                headers={"X-Forwarded-Proto": "https, http"},
            )
        assert_error(resp, 403, "HTTPS_REQUIRED")


class TestHttpsGuardTrustedProxy:
    async def test_xfp_from_untrusted_client_rejected(
        self, production_app_no_trusted_proxy,
    ):
        # `trusted_proxy_ips` пустой → XFP не доверяется ни от кого; даже от
        # 127.0.0.1 (т.е. от ASGITransport) cleartext + XFP=https → 403.
        async with AsyncClient(
            transport=ASGITransport(app=production_app_no_trusted_proxy),
            base_url="http://test",
        ) as ac:
            resp = await ac.get(
                f"{BASE}/servers",
                headers={"X-Forwarded-Proto": "https"},
            )
        assert_error(resp, 403, "HTTPS_REQUIRED")

    async def test_xfp_from_trusted_client_passes(self, production_app):
        # `TRUSTED_PROXY_IPS=127.0.0.1` → ASGITransport-клиент trusted,
        # его XFP=https проходит guard.
        async with AsyncClient(
            transport=ASGITransport(app=production_app), base_url="http://test",
        ) as ac:
            resp = await ac.get(
                f"{BASE}/servers",
                headers={"X-Forwarded-Proto": "https"},
            )
        assert (
            resp.status_code != 403
            or resp.json().get("error_code") != "HTTPS_REQUIRED"
        )


class TestHttpsGuardDev:
    async def test_cleartext_http_passes_in_dev(self, client):
        # Дефолтный модульный app — APP_ENV не выставлен (или test/local),
        # middleware выключен и пропускает всё. Endpoint вернёт 401 (нет
        # Authorization), но точно не 403 HTTPS_REQUIRED.
        resp = await client.get(f"{BASE}/servers")
        body = assert_error(resp, 401, "ACCESS_TOKEN_MISSING")
        assert body.get("error_code") != "HTTPS_REQUIRED"
