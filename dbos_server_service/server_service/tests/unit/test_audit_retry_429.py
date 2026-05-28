"""Retry-on-429 в `_send_to_logging_service` server_service.

Зеркало `auth_service/tests/services/test_audit_retry_429.py`.
"""

from __future__ import annotations

import httpx
import pytest

from src.services import audit_service


@pytest.fixture(autouse=True)
def _reset_counter():
    audit_service._reset_dropped_429_for_tests()
    yield
    audit_service._reset_dropped_429_for_tests()


@pytest.mark.asyncio
async def test_retry_succeeds_after_two_429(monkeypatch):
    """429 → 429 → 201: payload доезжает с правильным actor_type, ровно один success."""
    statuses = iter([429, 429, 201])
    received: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        code = next(statuses)
        if code == 201:
            import json as _json
            received.append(_json.loads(request.content.decode()))
            return httpx.Response(201, json={"accepted": True})
        return httpx.Response(429, json={"error": "rate_limited"})

    pooled = httpx.AsyncClient(
        base_url="http://loging-mock",
        transport=httpx.MockTransport(handler),
        timeout=2.0,
    )
    monkeypatch.setattr(audit_service, "_audit_client", pooled)

    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(audit_service.asyncio, "sleep", fake_sleep)

    try:
        await audit_service._send_to_logging_service(
            {"action": "server.power_on", "actor_type": "user"},
            "http://loging-mock",
            "k",
        )
    finally:
        await pooled.aclose()

    assert len(received) == 1
    assert received[0]["action"] == "server.power_on"
    assert received[0]["actor_type"] == "user"
    assert len(sleeps) == 2
    assert audit_service.get_dropped_429_total() == 0


@pytest.mark.asyncio
async def test_drop_after_three_429(monkeypatch, caplog):
    """429 × 3: counter +1, warning в логе."""
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        return httpx.Response(429, json={"error": "rate_limited"})

    pooled = httpx.AsyncClient(
        base_url="http://loging-mock",
        transport=httpx.MockTransport(handler),
        timeout=2.0,
    )
    monkeypatch.setattr(audit_service, "_audit_client", pooled)

    async def fake_sleep(delay: float) -> None:
        return None

    monkeypatch.setattr(audit_service.asyncio, "sleep", fake_sleep)

    import logging
    caplog.set_level(logging.WARNING, logger="audit")

    try:
        await audit_service._send_to_logging_service(
            {"action": "server.power_on"},
            "http://loging-mock",
            "k",
        )
    finally:
        await pooled.aclose()

    assert attempts["n"] == 3
    assert audit_service.get_dropped_429_total() == 1
    assert any("3x429" in rec.message for rec in caplog.records)


@pytest.mark.asyncio
async def test_success_first_attempt_no_sleep(monkeypatch):
    """201 на первой попытке — никаких retry и sleep'ов."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(201, json={"accepted": True})

    pooled = httpx.AsyncClient(
        base_url="http://loging-mock",
        transport=httpx.MockTransport(handler),
        timeout=2.0,
    )
    monkeypatch.setattr(audit_service, "_audit_client", pooled)

    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(audit_service.asyncio, "sleep", fake_sleep)

    try:
        await audit_service._send_to_logging_service(
            {"action": "server.power_on"}, "http://loging-mock", "k",
        )
    finally:
        await pooled.aclose()

    assert calls["n"] == 1
    assert sleeps == []
    assert audit_service.get_dropped_429_total() == 0


@pytest.mark.asyncio
async def test_non_429_5xx_no_retry(monkeypatch):
    """500 — не retryable, одна попытка, dropped_429 не растёт."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(500, json={"error": "boom"})

    pooled = httpx.AsyncClient(
        base_url="http://loging-mock",
        transport=httpx.MockTransport(handler),
        timeout=2.0,
    )
    monkeypatch.setattr(audit_service, "_audit_client", pooled)

    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(audit_service.asyncio, "sleep", fake_sleep)

    try:
        await audit_service._send_to_logging_service(
            {"action": "server.power_on"}, "http://loging-mock", "k",
        )
    finally:
        await pooled.aclose()

    assert calls["n"] == 1
    assert sleeps == []
    assert audit_service.get_dropped_429_total() == 0


@pytest.mark.asyncio
async def test_fallback_path_retries_on_429(monkeypatch):
    """Per-call fallback path тоже должен ретраить 429."""
    monkeypatch.setattr(audit_service, "_audit_client", None)

    statuses = iter([429, 201])
    received: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        code = next(statuses)
        if code == 201:
            import json as _json
            received.append(_json.loads(request.content.decode()))
            return httpx.Response(201, json={"accepted": True})
        return httpx.Response(429, json={"error": "rate_limited"})

    transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs.pop("transport", None)
        return real_async_client(transport=transport, **kwargs)

    monkeypatch.setattr(audit_service.httpx, "AsyncClient", factory)

    async def fake_sleep(delay: float) -> None:
        return None

    monkeypatch.setattr(audit_service.asyncio, "sleep", fake_sleep)

    await audit_service._send_to_logging_service(
        {"action": "server.power_on"}, "http://loging-mock", "k",
    )

    assert len(received) == 1
    assert audit_service.get_dropped_429_total() == 0
