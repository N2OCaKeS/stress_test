"""Тесты счётчика dropped_429 в audit_service.

Дополняет test_audit_retry_429.py.
Фокус на поведении счётчика:
* Счётчик инкрементируется на +1 за каждый drop (не +3 за три 429).
* Несколько последовательных дропов накапливаются.
* _reset_dropped_429_for_tests сбрасывает в 0.
* get_dropped_429_total() при 0 дропах = 0 (baseline).
* Успешная доставка после нескольких 429 не увеличивает счётчик.
* HTTPError не увеличивает счётчик (не тот код пути).
"""

from __future__ import annotations

import httpx
import pytest
from unittest.mock import AsyncMock

from src.services import audit_service


async def _noop_sleep(_delay):
    return None


@pytest.fixture(autouse=True)
def _reset_counter():
    audit_service._reset_dropped_429_for_tests()
    yield
    audit_service._reset_dropped_429_for_tests()


@pytest.mark.asyncio
async def test_single_drop_increments_counter_by_one(monkeypatch):
    """Три подряд 429 → один drop → счётчик +1, не +3."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": "rate_limited"})

    pooled = httpx.AsyncClient(
        base_url="http://loging-mock",
        transport=httpx.MockTransport(handler),
        timeout=2.0,
    )
    monkeypatch.setattr(audit_service, "_audit_client", pooled)
    monkeypatch.setattr(audit_service.asyncio, "sleep", _noop_sleep)

    try:
        await audit_service._send_to_logging_service(
            {"action": "test.action"}, "http://loging-mock", "k",
        )
    finally:
        await pooled.aclose()

    assert audit_service.get_dropped_429_total() == 1


@pytest.mark.asyncio
async def test_multiple_drops_accumulate(monkeypatch):
    """Каждый вызов со всеми 429 добавляет +1 к счётчику."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": "rate_limited"})

    pooled = httpx.AsyncClient(
        base_url="http://loging-mock",
        transport=httpx.MockTransport(handler),
        timeout=2.0,
    )
    monkeypatch.setattr(audit_service, "_audit_client", pooled)
    monkeypatch.setattr(audit_service.asyncio, "sleep", _noop_sleep)

    try:
        for _ in range(3):
            await audit_service._send_to_logging_service(
                {"action": "test.batch"}, "http://loging-mock", "k",
            )
    finally:
        await pooled.aclose()

    assert audit_service.get_dropped_429_total() == 3


@pytest.mark.asyncio
async def test_reset_clears_accumulated_counter(monkeypatch):
    """Сброс между прогонами работает изолированно."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": "rate_limited"})

    pooled = httpx.AsyncClient(
        base_url="http://loging-mock",
        transport=httpx.MockTransport(handler),
        timeout=2.0,
    )
    monkeypatch.setattr(audit_service, "_audit_client", pooled)
    monkeypatch.setattr(audit_service.asyncio, "sleep", _noop_sleep)

    try:
        await audit_service._send_to_logging_service(
            {"action": "test.action1"}, "http://loging-mock", "k",
        )
        assert audit_service.get_dropped_429_total() == 1
        audit_service._reset_dropped_429_for_tests()
        assert audit_service.get_dropped_429_total() == 0
        await audit_service._send_to_logging_service(
            {"action": "test.action2"}, "http://loging-mock", "k",
        )
        assert audit_service.get_dropped_429_total() == 1
    finally:
        await pooled.aclose()


@pytest.mark.asyncio
async def test_success_after_retries_does_not_increment_counter(monkeypatch):
    """429 → 429 → 201: счётчик остаётся 0."""
    statuses = iter([429, 429, 201])

    def handler(request: httpx.Request) -> httpx.Response:
        code = next(statuses)
        return httpx.Response(code, json={"ok": True} if code == 201 else {"error": "rl"})

    pooled = httpx.AsyncClient(
        base_url="http://loging-mock",
        transport=httpx.MockTransport(handler),
        timeout=2.0,
    )
    monkeypatch.setattr(audit_service, "_audit_client", pooled)
    monkeypatch.setattr(audit_service.asyncio, "sleep", _noop_sleep)

    try:
        await audit_service._send_to_logging_service(
            {"action": "test.success"}, "http://loging-mock", "k",
        )
    finally:
        await pooled.aclose()

    assert audit_service.get_dropped_429_total() == 0


@pytest.mark.asyncio
async def test_http_error_does_not_increment_counter(monkeypatch):
    """Transport error (HTTPError) — не drop, счётчик не трогается."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    pooled = httpx.AsyncClient(
        base_url="http://loging-mock",
        transport=httpx.MockTransport(handler),
        timeout=2.0,
    )
    monkeypatch.setattr(audit_service, "_audit_client", pooled)

    try:
        await audit_service._send_to_logging_service(
            {"action": "test.transport_err"}, "http://loging-mock", "k",
        )
    finally:
        await pooled.aclose()

    assert audit_service.get_dropped_429_total() == 0


def test_baseline_counter_is_zero():
    """При старте (после reset фикстуры) счётчик = 0."""
    assert audit_service.get_dropped_429_total() == 0
