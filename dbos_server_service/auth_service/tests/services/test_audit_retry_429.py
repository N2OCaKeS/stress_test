"""Retry-on-429 в `_send_to_logging_service`.

При rate-limit от loging_service emit делает до двух дополнительных попыток
с exponential backoff + jitter. После трёх 429 подряд событие отбрасывается
и инкрементится `_audit_dropped_429`. На 2xx первой попытки никаких задержек
не должно быть.
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

    # Жмём backoff в ноль, чтобы тест не ждал ~2s.
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(audit_service.asyncio, "sleep", fake_sleep)

    try:
        await audit_service._send_to_logging_service(
            {"action": "user.login", "actor_type": "user"},
            "http://loging-mock",
            "k",
        )
    finally:
        await pooled.aclose()

    assert len(received) == 1
    assert received[0]["action"] == "user.login"
    assert received[0]["actor_type"] == "user"
    # Два backoff'а перед двумя retry'ами.
    assert len(sleeps) == 2
    assert audit_service.get_dropped_429_total() == 0


@pytest.mark.asyncio
async def test_drop_after_three_429(monkeypatch):
    """429 × 3: ровно 3 попытки, drop-counter +1, без crash'а."""
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

    try:
        await audit_service._send_to_logging_service(
            {"action": "user.login"},
            "http://loging-mock",
            "k",
        )
    finally:
        await pooled.aclose()

    # 3 попытки (исходная + 2 retry) + инкремент public-counter — это и есть
    # контракт drop'а после исчерпания 429-budget'а.
    assert attempts["n"] == 3
    assert audit_service.get_dropped_429_total() == 1


@pytest.mark.asyncio
async def test_success_first_attempt_no_sleep(monkeypatch):
    """201 на первой попытке — никаких asyncio.sleep / retry."""
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
            {"action": "user.login"}, "http://loging-mock", "k",
        )
    finally:
        await pooled.aclose()

    assert calls["n"] == 1
    assert sleeps == []
    assert audit_service.get_dropped_429_total() == 0


@pytest.mark.asyncio
async def test_non_429_4xx_no_retry(monkeypatch):
    """403/500 не считаются retryable — одна попытка, без drop-counter'а."""
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
            {"action": "user.login"}, "http://loging-mock", "k",
        )
    finally:
        await pooled.aclose()

    assert calls["n"] == 1
    assert sleeps == []
    # 5xx не релевантен этому счётчику — только 429.
    assert audit_service.get_dropped_429_total() == 0


@pytest.mark.asyncio
async def test_fallback_path_retries_on_429(monkeypatch):
    """Когда `_audit_client is None` (per-call fallback) — retry тоже работает."""
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
        {"action": "user.login"}, "http://loging-mock", "k",
    )

    assert len(received) == 1
    assert audit_service.get_dropped_429_total() == 0
