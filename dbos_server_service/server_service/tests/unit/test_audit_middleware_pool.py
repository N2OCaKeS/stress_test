"""Unit-тесты: `audit_access` middleware шлёт audit-event через pooled
`_audit_client` (а не per-call `httpx.AsyncClient`).

Контекст:

* раньше `audit_access` оборачивал emit в `asyncio.to_thread(emit, ...)`,
  поэтому worker-thread видел `RuntimeError` от `asyncio.get_running_loop()`
  и шёл в sync-fallback `httpx.post(...)` — каждый раз новый коннект,
  pool из lifespan игнорировался;
* фикс: middleware вызывает `audit_service.emit(...)` прямо в running
  event-loop. `emit` детектит loop через `asyncio.get_running_loop()`,
  планирует `loop.create_task(_send_to_logging_service(...))`, и
  pooled-путь работает.

Тест ставит `_audit_client = httpx.AsyncClient(MockTransport)` и
проверяет, что счётчик `httpx.AsyncClient.__init__` под N-запросом
4xx-трафика остаётся равным 0 (т.е. ни один новый клиент не создаётся
в hot-path). До фикса было бы N (по одному per emit'у).
"""

from __future__ import annotations

import asyncio
import httpx
import pytest

from src.services import audit_service


@pytest.mark.asyncio
async def test_audit_middleware_emit_uses_pool_not_to_thread(monkeypatch):
    """Эмулируем emit() из running loop → счётчик `AsyncClient.__init__` НЕ растёт.

    Это тот же путь, что использует `audit_access` middleware после фикса:
    `audit_service.emit(...)` вызывается из async-контекста, видит loop,
    шедулит task через pooled клиент.
    """
    requests_seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests_seen.append(str(request.url))
        return httpx.Response(202, json={"accepted": True})

    pooled = httpx.AsyncClient(
        base_url="http://loging-mock",
        transport=httpx.MockTransport(handler),
        timeout=2.0,
    )

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

    # Счётчик новых httpx.AsyncClient. ЛЮБОЙ new клиент здесь — индикатор
    # того, что мы вернулись на sync-fallback / per-call путь.
    new_client_counter = {"created": 0}
    real_async_client = httpx.AsyncClient

    def counting_factory(*args, **kwargs):
        new_client_counter["created"] += 1
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(audit_service.httpx, "AsyncClient", counting_factory)

    try:
        # 10 audit-emit'ов в running loop (имитируем `audit_access` middleware).
        # `emit()` — sync функция, она внутри сама делает loop.create_task.
        for i in range(10):
            audit_service.emit(
                "http.client_error",
                status="failure",
                allowed=False,
                details={"i": i},
            )

        # Дать loop'у обработать запланированные task'и.
        # `loop.create_task(...)` не блокирует — нужно явно отдать управление.
        for _ in range(10):
            await asyncio.sleep(0)

        # Все 10 emit'ов должны были долететь до MockTransport.
        assert len(requests_seen) == 10, (
            f"только {len(requests_seen)}/10 emit'ов долетели через pool "
            f"(остальные ушли в sync-fallback?)"
        )

        # КЛЮЧЕВОЕ: ни одного нового httpx.AsyncClient.
        # До фикса: каждый emit через `to_thread` шёл в sync-fallback и
        # создавал свежий клиент → counter был 10.
        assert new_client_counter["created"] == 0, (
            f"emit'ы из running loop создали {new_client_counter['created']} "
            f"новых httpx.AsyncClient — индикатор regression'а к per-call пути."
        )
    finally:
        await pooled.aclose()


@pytest.mark.asyncio
async def test_emit_in_to_thread_falls_back_to_sync_path(monkeypatch):
    """Документация: emit в `asyncio.to_thread` НЕ видит loop → sync-fallback.

    Worker-thread не имеет running event-loop, `asyncio.get_running_loop()`
    → `RuntimeError`, emit идёт в sync-`httpx.post`, pool из lifespan
    игнорируется.

    Тест демонстрирует поведение — НЕ для regression-защиты, а как точка
    отсчёта: если поведение когда-то изменится (например, обернёт sync
    тоже в pool), тест покажет это.
    """
    sync_calls: list[dict] = []

    def fake_sync_post(url, json, headers, timeout):  # noqa: ARG001
        sync_calls.append({"url": url, "json": json})

        class _Resp:
            status_code = 202
        return _Resp()

    fake_settings = type(
        "FakeSettings",
        (),
        {
            "logging_service_url": "http://loging-mock",
            "logging_service_api_key": "test-audit-key",
        },
    )()
    monkeypatch.setattr(audit_service, "get_settings", lambda: fake_settings)
    monkeypatch.setattr(audit_service, "_audit_client", None)  # вне lifespan
    monkeypatch.setattr(audit_service.httpx, "post", fake_sync_post)

    # to_thread → worker-thread, нет running loop → sync-fallback.
    await asyncio.to_thread(
        audit_service.emit,
        "http.client_error",
        status="failure",
        allowed=False,
        details={"key": "val"},
    )

    # Sync-path сработал, потому что в to_thread нет running loop.
    assert len(sync_calls) == 1
    assert sync_calls[0]["url"].endswith("/api/logging/v1/events")
