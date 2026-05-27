"""Integration: подтверждает, что `attach_request_id_and_context` middleware в
`src/main.py` использует helper `services/audit_context.extract_client_ip` с
allow-list `Settings.trusted_proxy_ips`, а НЕ старый наивный XFF-парсер.

Стратегия:
- Конструируем приложение `create_application()` со специально настроенным
  значением `trusted_proxy_ips` (через monkeypatch `get_settings` + сброс
  lru_cache).
- Гоняем запросы через `AsyncClient` с `ASGITransport(client=(host, port))` —
  это устанавливает `request.client.host`.
- Перехватываем `audit_service.emit` (sync httpx.post) и читаем
  `ip_address` из опубликованного payload — это и есть значение,
  которое middleware положил в `AuditContext`.

Кейсы:
  1. `request.client.host` НЕ в `trusted_proxy_ips` + есть XFF →
     ip_address == request.client.host (старый код вернул бы leftmost XFF).
  2. `request.client.host` ∈ `trusted_proxy_ips` + XFF с цепочкой →
     ip_address == left-most non-trusted из XFF.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator

import pytest
from httpx import ASGITransport, AsyncClient

from src.core.config import get_settings
from src.dependencies.db import get_db
from src.main import create_application


# ── Перехват audit-payload (только sync-путь нас интересует) ─────────────────


@pytest.fixture()
def capture_audit_payloads(monkeypatch) -> list[dict]:
    """Собирает payload, которые `audit_service.emit` отправляет в loging_service.

    middleware `audit_access` вложен в context-middleware и эмитит прямо в
    running event-loop'е через `loop.create_task(_send_to_logging_service)`,
    которая постит в `httpx.AsyncClient` (здесь подменён на mock). Контекст
    (actor/ip/ua) жив на момент emit'а, поэтому попадает в payload.
    """
    captured: list[dict] = []

    class _AsyncClient:
        def __init__(self, *a, **k):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            pass
        async def post(self, url, json, headers):
            captured.append(json)
            class R:
                status_code = 201
            return R()

    monkeypatch.setattr("src.services.audit_service.httpx.AsyncClient", _AsyncClient)
    monkeypatch.setattr(
        "src.services.audit_service.get_settings",
        lambda: type("S", (), {
            "logging_service_url": "http://test", "logging_service_api_key": "k",
        })(),
    )
    return captured


# ── Фикстура: пересоздаём app с подменённым trusted_proxy_ips ───────────────


@pytest.fixture()
def trusted_proxies_env(monkeypatch) -> Iterator[None]:
    """Устанавливает TRUSTED_PROXY_IPS=["10.0.0.1"] в env и сбрасывает кэш Settings,
    чтобы `create_application()` подхватило новое значение. pydantic-settings
    парсит env для list[str] полей до field_validator, поэтому формат — JSON."""
    monkeypatch.setenv("TRUSTED_PROXY_IPS", '["10.0.0.1"]')
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def _noop_get_db():  # pragma: no cover — переопределяет реальный get_db
    """`/me` зависит от AsyncSession, но для не-аутентифицированного запроса
    (401 до обращения к БД) сам объект сессии не используется. Возвращаем None,
    чтобы avoid реальное подключение."""
    yield None


def _build_client(fake_client_host: str) -> AsyncClient:
    """Собирает изолированный AsyncClient: своё приложение, ASGITransport
    с переопределённым `request.client.host = fake_client_host`."""
    app = create_application()
    app.dependency_overrides[get_db] = _noop_get_db
    transport = ASGITransport(app=app, client=(fake_client_host, 12345))
    return AsyncClient(transport=transport, base_url="http://test")


# ── Тесты ─────────────────────────────────────────────────────────────────────


class TestMainExtractsClientIpViaHelper:
    """Прямые свидетельства: middleware вызывает `extract_client_ip` helper,
    а не старый наивный парсер из main.py."""

    async def test_untrusted_client_xff_is_ignored_uses_direct_host(
        self, trusted_proxies_env, capture_audit_payloads,
    ):
        """request.client.host НЕ в trusted_proxy_ips → XFF полностью игнорируется,
        ip_address == прямой client.host. Старый код вернул бы '1.2.3.4'."""
        async with _build_client(
            fake_client_host="203.0.113.42"
        ) as ac:
            r = await ac.get(
                "/api/auth/v1/me",  # без Authorization → 401
                headers={"X-Forwarded-For": "1.2.3.4, 5.6.7.8"},
            )
            assert r.status_code == 401
            # emit планирует доставку через loop.create_task — дренируем.
            await asyncio.sleep(0)

        denied = [p for p in capture_audit_payloads
                  if p["action"] == "http.access_denied"]
        assert len(denied) == 1, capture_audit_payloads
        # КРИТИЧЕСКАЯ проверка: ip — это direct client.host, а НЕ leftmost из XFF.
        emitted_ip = denied[0]["details"].get("ip")
        assert emitted_ip == "203.0.113.42", (
            f"middleware подхватил XFF от недоверенного клиента — helper НЕ подключён. "
            f"emitted ip={emitted_ip!r}"
        )
        assert emitted_ip != "1.2.3.4"

    async def test_trusted_client_xff_is_parsed_leftmost_nontrusted(
        self, trusted_proxies_env, capture_audit_payloads,
    ):
        """request.client.host ∈ trusted_proxy_ips → парсим XFF; берём
        самый левый non-trusted IP."""
        async with _build_client(
            fake_client_host="10.0.0.1"
        ) as ac:
            r = await ac.get(
                "/api/auth/v1/me",
                headers={"X-Forwarded-For": "198.51.100.7, 10.0.0.1"},
            )
            assert r.status_code == 401
            await asyncio.sleep(0)

        denied = [p for p in capture_audit_payloads
                  if p["action"] == "http.access_denied"]
        assert len(denied) == 1, capture_audit_payloads
        emitted_ip = denied[0]["details"].get("ip")
        # ip == left-most non-trusted: реальный клиент за доверенным proxy.
        assert emitted_ip == "198.51.100.7", (
            f"middleware не парсит XFF от доверенного proxy. "
            f"emitted ip={emitted_ip!r}"
        )

    async def test_no_xff_falls_back_to_direct_host(
        self, trusted_proxies_env, capture_audit_payloads,
    ):
        """Без XFF/X-Real-IP — направление client.host используется в обоих режимах."""
        async with _build_client(
            fake_client_host="198.51.100.99"
        ) as ac:
            r = await ac.get("/api/auth/v1/me")
            assert r.status_code == 401
            await asyncio.sleep(0)

        denied = [p for p in capture_audit_payloads
                  if p["action"] == "http.access_denied"]
        assert len(denied) == 1
        assert denied[0]["details"].get("ip") == "198.51.100.99"
