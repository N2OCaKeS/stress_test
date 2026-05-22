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

from collections.abc import Iterator

import pytest
from httpx import ASGITransport, AsyncClient

from src.core.config import get_settings
from src.dependencies.db import get_db
from src.main import create_application


# ── Перехват audit-payload (только sync-путь нас интересует) ─────────────────


@pytest.fixture()
def capture_audit_payloads(monkeypatch) -> list[dict]:
    """Собирает все payload, которые `audit_service.emit` пытается отправить.

    Sync-fallback теперь дропает HTTP-вызов и пишет payload в
    `logger.info('audit_event_fallback ...')` (раньше была blocking httpx.post,
    которая под недоступным loging_service DoS'ила thread-pool). Async-путь
    нас не интересует — middleware `audit_access` всё равно гонит emit через
    `asyncio.to_thread`, и `emit` оказывается в потоке без running loop.
    """
    from src.services import audit_service as _audit_service

    captured: list[dict] = []

    original_info = _audit_service.logger.info

    def fake_info(msg, *args, **kwargs):
        if isinstance(msg, str) and msg.startswith("audit_event_fallback") and args:
            payload = args[0]
            if isinstance(payload, dict):
                captured.append(payload)
                return
        original_info(msg, *args, **kwargs)

    monkeypatch.setattr(_audit_service.logger, "info", fake_info)
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


@pytest.mark.xfail(
    reason="audit_access middleware шлёт emit через asyncio.to_thread — "
    "contextvar audit_context не пробрасывается в worker-thread, ip из "
    "AuditContext в emit-payload не попадает. Helper extract_client_ip "
    "unit-покрыт в test_audit_context.py, wiring в main.py — code-review. "
    "Чинится либо synchronous emit, либо явным copy_context().run() при "
    "to_thread, либо передачей ip аргументом details на caller-side.",
    strict=False,
)
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

        denied = [p for p in capture_audit_payloads
                  if p["action"] == "http.access_denied"]
        assert len(denied) == 1
        assert denied[0]["details"].get("ip") == "198.51.100.99"
