"""Unit-тесты s2s-пути drift-клиента: `fetch_drift_events` ходит в loging
internal read-канал с service-key + `X-Service-Identity`.

Раньше клиент бил в публичный `GET /events`, который требует user-bearer
с `loging_admin`/`loging_reader`, и service-key отбивался 401 → drift отдавал
503 `LOGING_SERVICE_AUTH_FAILED`. Теперь путь — `/api/logging/v1/internal/events`
под тем же shared-secret и `X-Service-Identity: server_service`, что и
write-канал.

Перехватываем исходящий запрос `httpx.MockTransport`'ом и проверяем path,
header'ы и query-параметры. Auth-denied ветка (loging вернул 401) проверяет,
что клиент по-прежнему поднимает `LOGING_SERVICE_AUTH_FAILED`.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest

from src.core import http_clients
from src.core.exceptions import ServiceUnavailableError
from src.services import loging_client


def _fake_settings():
    return type(
        "FakeSettings",
        (),
        {
            "logging_service_url": "http://loging-mock",
            "logging_service_api_key": "test-read-key",
        },
    )()


@pytest.mark.asyncio
async def test_fetch_drift_uses_internal_path_and_service_identity(monkeypatch):
    """Happy-path: запрос уходит на /internal/events с bearer + identity."""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["authorization"] = request.headers.get("Authorization")
        captured["identity"] = request.headers.get("X-Service-Identity")
        captured["params"] = dict(request.url.params)
        return httpx.Response(
            200,
            json={
                "items": [
                    {"target_id": "srv_test", "details": {"login": "alice"}},
                    {"target_id": "srv_other", "details": {"login": "bob"}},
                ],
                "total": None,
                "has_more": False,
            },
        )

    pooled = httpx.AsyncClient(
        base_url="http://loging-mock",
        transport=httpx.MockTransport(handler),
        timeout=5.0,
    )
    monkeypatch.setattr(loging_client, "get_settings", _fake_settings)
    monkeypatch.setattr(http_clients, "loging_read_client", pooled)

    since = datetime.now(timezone.utc) - timedelta(hours=1)
    try:
        events, truncated = await loging_client.fetch_drift_events(
            server_id="srv_test", since=since,
        )
    finally:
        await pooled.aclose()

    assert captured["path"] == "/api/logging/v1/internal/events"
    assert captured["authorization"] == "Bearer test-read-key"
    assert captured["identity"] == "server_service"
    assert captured["params"]["action"] == "server_account.drift_detected"
    assert captured["params"]["target_id"] == "srv_test"
    # Локальный фильтр оставляет только события целевого сервера.
    assert len(events) == 1
    assert events[0]["target_id"] == "srv_test"
    assert truncated is False


@pytest.mark.asyncio
async def test_fetch_drift_auth_denied_raises_auth_failed(monkeypatch):
    """loging вернул 401 → `LOGING_SERVICE_AUTH_FAILED` (operator-config error)."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error_code": "INVALID_SERVICE_KEY"})

    pooled = httpx.AsyncClient(
        base_url="http://loging-mock",
        transport=httpx.MockTransport(handler),
        timeout=5.0,
    )
    monkeypatch.setattr(loging_client, "get_settings", _fake_settings)
    monkeypatch.setattr(http_clients, "loging_read_client", pooled)

    since = datetime.now(timezone.utc) - timedelta(hours=1)
    try:
        with pytest.raises(ServiceUnavailableError) as exc_info:
            await loging_client.fetch_drift_events(server_id="srv_test", since=since)
    finally:
        await pooled.aclose()

    assert exc_info.value.error_code == "LOGING_SERVICE_AUTH_FAILED"
