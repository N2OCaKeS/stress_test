"""Unit-тест: module-level `loging_read_client` переиспользуется между
последовательными `fetch_drift_events` вызовами.

До F22-B каждый `GET /servers/{id}/drift` открывал свежий `httpx.AsyncClient`
через `async with` — на дашборде с N серверами это N TCP+TLS-handshake'ов
на refresh. Сейчас pooled клиент поднимается в `main.lifespan` startup
(см. `core/http_clients.py::loging_read_client`), а fetch использует его
без создания нового `AsyncClient` внутри hot-path.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest

from src.core import http_clients
from src.services import loging_client


@pytest.mark.asyncio
async def test_fetch_drift_events_reuses_pooled_client(monkeypatch):
    """Два последовательных вызова `fetch_drift_events` идут через один pooled-client.

    Инвариант: при заранее поднятом `http_clients.loging_read_client`
    `httpx.AsyncClient.__init__` НЕ должен вызываться внутри fetch'а —
    переиспользуем pooled TCP-сессию.
    """
    requests_seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests_seen.append(str(request.url))
        return httpx.Response(
            200,
            json={"items": [], "total": None, "cursor": None},
        )

    pooled = httpx.AsyncClient(
        base_url="http://loging-mock",
        transport=httpx.MockTransport(handler),
        timeout=5.0,
    )

    fake_settings = type(
        "FakeSettings",
        (),
        {
            "logging_service_url": "http://loging-mock",
            "logging_service_api_key": "test-read-key",
        },
    )()
    monkeypatch.setattr(loging_client, "get_settings", lambda: fake_settings)
    monkeypatch.setattr(http_clients, "loging_read_client", pooled)

    new_client_counter = {"created": 0}
    real_async_client = httpx.AsyncClient

    def counting_factory(*args, **kwargs):
        new_client_counter["created"] += 1
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(loging_client.httpx, "AsyncClient", counting_factory)

    since = datetime.now(timezone.utc) - timedelta(hours=1)
    try:
        events_a, truncated_a = await loging_client.fetch_drift_events(
            server_id="srv_test", since=since,
        )
        events_b, truncated_b = await loging_client.fetch_drift_events(
            server_id="srv_test", since=since,
        )
        assert events_a == [] and events_b == []
        assert truncated_a is False and truncated_b is False
        # Оба запроса дошли до MockTransport
        assert len(requests_seen) == 2
        # Но НИ ОДНОГО нового AsyncClient не создано — переиспользуем pooled
        assert new_client_counter["created"] == 0
    finally:
        await pooled.aclose()
