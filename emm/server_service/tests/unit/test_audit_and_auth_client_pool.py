"""Unit-тесты: module-level `_audit_client` переиспользуется
вместо `httpx.AsyncClient` per-call (FD-amplification фикс).

Парный к фиксу:
* `services/audit_service.py:_audit_client` — pooled для loging_service
* `main.lifespan` — startup инициализирует pool + shutdown закрывает

Slowloris-amplification часть «после round-2»: один attacker request на
401-pipeline → 1 introspect (pooled, уже закрыто) + 1 audit emission
(per-call, FD-leak). Этот фикс закрывает оставшийся вектор.

Тесты НЕ запускают сетевые roundtrip'ы — `MockTransport` имитирует
loging_service. Проверяется invariant: при заранее
поднятом pooled-client'е `httpx.AsyncClient.__init__` НЕ должен быть
вызван внутри hot-path функций.
"""

from __future__ import annotations

import httpx
import pytest

from src.services import audit_service


# ── audit_service.emit pooled path ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_audit_emit_reuses_pooled_client(monkeypatch):
    """5 последовательных emit() → `httpx.AsyncClient.__init__` НЕ создан ни разу.

    Когда `_audit_client` уже инициализирован (как в lifespan startup),
    `_send_to_logging_service` должен идти по pooled-пути и переиспользовать
    одну TCP-сессию. Создание нового клиента — индикатор regression'а к
    per-call поведению.
    """
    requests_seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json as _json
        requests_seen.append({
            "url": str(request.url),
            "body": _json.loads(request.content.decode()),
            "auth": request.headers.get("Authorization"),
        })
        return httpx.Response(202, json={"accepted": True})

    pooled = httpx.AsyncClient(
        base_url="http://loging-mock",
        transport=httpx.MockTransport(handler),
        timeout=2.0,
    )

    # Подменяем настройки — pooled-путь не использует logging_service_url
    # для построения URL (base_url уже на клиенте), но `emit()` всё ещё
    # читает settings для shortcut "не настроен → локальный лог".
    fake_settings = type(
        "FakeSettings",
        (),
        {
            "logging_service_url": "http://loging-mock",
            "logging_service_api_key": "test-audit-key",
        },
    )()
    monkeypatch.setattr(audit_service, "get_settings", lambda: fake_settings)
    monkeypatch.setattr(audit_service, "_audit_client", pooled)

    # Счётчик создаваемых клиентов. Если фикс работает — НИ ОДНОГО внутри
    # _send_to_logging_service (pooled путь). До фикса было бы 5.
    new_client_counter = {"created": 0}
    real_async_client = httpx.AsyncClient

    def counting_factory(*args, **kwargs):
        new_client_counter["created"] += 1
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(audit_service.httpx, "AsyncClient", counting_factory)

    try:
        # 5 последовательных вызовов через emit() в sync-режиме (без event loop).
        # `emit()` сам поднимает task, поэтому здесь дёргаем _send_to_logging_service
        # напрямую — проверяем именно его pooled-путь.
        for i in range(5):
            await audit_service._send_to_logging_service(
                {"action": f"test.event_{i}"},
                "http://loging-mock",
                "test-audit-key",
            )
        assert len(requests_seen) == 5
        # Ключевой инвариант: НИ ОДНОГО нового httpx.AsyncClient.
        assert new_client_counter["created"] == 0
        # Sanity: auth header передан, body доехал.
        assert all(r["auth"] == "Bearer test-audit-key" for r in requests_seen)
        assert requests_seen[0]["url"].endswith("/api/logging/v1/events")
    finally:
        await pooled.aclose()


@pytest.mark.asyncio
async def test_audit_send_falls_back_to_per_call_when_pool_uninitialised(monkeypatch):
    """Когда `_audit_client is None` (вне lifespan) — fallback создаёт per-call.

    Обязательное поведение для unit-тестов, импортирующих модуль до старта app.
    """
    monkeypatch.setattr(audit_service, "_audit_client", None)

    new_client_counter = {"created": 0}
    real_async_client = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(202, json={"accepted": True})

    transport = httpx.MockTransport(handler)

    def counting_factory(*args, **kwargs):
        new_client_counter["created"] += 1
        kwargs.pop("transport", None)
        return real_async_client(transport=transport, **kwargs)

    monkeypatch.setattr(audit_service.httpx, "AsyncClient", counting_factory)

    # Два emit'а — два клиента должно быть создано (fallback per-call).
    await audit_service._send_to_logging_service(
        {"action": "x"}, "http://loging-mock", "key"
    )
    await audit_service._send_to_logging_service(
        {"action": "y"}, "http://loging-mock", "key"
    )
    assert new_client_counter["created"] == 2


# ── lifespan integration: audit-pool поднимается и закрывается ──────────────


@pytest.mark.asyncio
async def test_lifespan_initialises_audit_pool(monkeypatch):
    """После старта lifespan module-level audit-клиент — живой AsyncClient."""
    from src.core import config as config_mod
    from src.main import create_application

    # Задаём logging_service_url через env — audit-pool conditional skip
    # если url пустой; для теста нужно явно непустое значение.
    monkeypatch.setenv("LOGGING_SERVICE_URL", "http://logging.test.local")
    config_mod.get_settings.cache_clear()

    # Заглушаем синхронный startup-аудит — он шлёт live httpx запросы наружу
    # (register_events + service.started emit). Подмена на async-noop.
    async def _noop_startup() -> None:
        return None
    monkeypatch.setattr("src.main._run_startup_audit_sequence", _noop_startup)

    # Сбрасываем module-level state перед стартом.
    audit_service._audit_client = None

    app = create_application()
    async with app.router.lifespan_context(app):
        assert audit_service._audit_client is not None
        assert isinstance(audit_service._audit_client, httpx.AsyncClient)
        assert audit_service._audit_client.is_closed is False

    # После shutdown — обнулён и закрыт.
    assert audit_service._audit_client is None


@pytest.mark.asyncio
async def test_lifespan_shutdown_closes_audit_pool(monkeypatch):
    """`aclose()` действительно вызывается на shutdown для audit-pool'а."""
    from src.core import config as config_mod
    from src.main import create_application

    monkeypatch.setenv("LOGGING_SERVICE_URL", "http://logging.test.local")
    config_mod.get_settings.cache_clear()

    async def _noop_startup() -> None:
        return None
    monkeypatch.setattr("src.main._run_startup_audit_sequence", _noop_startup)

    audit_service._audit_client = None

    captured: dict = {}

    app = create_application()
    async with app.router.lifespan_context(app):
        captured["audit"] = audit_service._audit_client
        assert captured["audit"] is not None

    assert captured["audit"].is_closed is True
