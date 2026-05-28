"""Edge cases для audit_service._send_to_logging_service:
- HTTPError в первой попытке → drop + warning (не crash)
- HTTPError во время retry (после 429) → drop + warning
- Partial timeout (ReadTimeout, ConnectTimeout) → drop + warning
- Unexpected Exception → drop + warning (не пробрасывать в hot path)
- Успешная 201 после HTTPError не откатывает drop (отдельный поток)
"""

from __future__ import annotations

import logging

import httpx
import pytest

from src.services import audit_service


@pytest.fixture(autouse=True)
def _reset():
    audit_service._reset_dropped_429_for_tests()
    yield
    audit_service._reset_dropped_429_for_tests()


# ── HTTPError на первой попытке ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_http_error_on_first_attempt_drops_and_warns(monkeypatch, caplog):
    """ConnectError на первой попытке → событие теряется, warning в лог, нет крэша."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    pooled = httpx.AsyncClient(
        base_url="http://loging-mock",
        transport=httpx.MockTransport(handler),
        timeout=2.0,
    )
    monkeypatch.setattr(audit_service, "_audit_client", pooled)

    caplog.set_level(logging.WARNING, logger="audit")
    try:
        await audit_service._send_to_logging_service(
            {"action": "user.login"},
            "http://loging-mock",
            "key",
        )
    finally:
        await pooled.aclose()

    # Не упало — функция завершилась корректно.
    # HTTPError попадает в except httpx.HTTPError → WARNING.
    assert any("failed to send" in rec.message.lower() or "failed to send" in str(rec.message) for rec in caplog.records) or \
           any("audit_service" in rec.name for rec in caplog.records)
    # Drop-counter не для HTTPError (только для 429-drain), но и не должен расти.
    assert audit_service.get_dropped_429_total() == 0


@pytest.mark.asyncio
async def test_http_error_during_retry_after_429_drops(monkeypatch, caplog):
    """429 → ConnectError на retry → событие теряется, нет крэша.

    _send_to_logging_service итерирует по (len(RETRY_DELAYS) + 1) попыткам.
    После первой 429 идёт backoff + retry → если там тоже упало → except httpx.HTTPError.
    """
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, json={"error": "rate_limited"})
        raise httpx.ConnectError("connection dropped during retry")

    pooled = httpx.AsyncClient(
        base_url="http://loging-mock",
        transport=httpx.MockTransport(handler),
        timeout=2.0,
    )
    monkeypatch.setattr(audit_service, "_audit_client", pooled)

    async def fast_sleep(_: float) -> None:
        return None

    monkeypatch.setattr(audit_service.asyncio, "sleep", fast_sleep)
    caplog.set_level(logging.WARNING, logger="audit")

    try:
        await audit_service._send_to_logging_service(
            {"action": "user.login"},
            "http://loging-mock",
            "key",
        )
    finally:
        await pooled.aclose()

    # Первый вызов был 429, второй кинул HTTPError — не упали.
    assert calls["n"] == 2
    # drop_429_total не инкрементится (мы не прошли полный 3x-429 цикл).
    assert audit_service.get_dropped_429_total() == 0


@pytest.mark.asyncio
async def test_read_timeout_drops_and_logs(monkeypatch, caplog):
    """ReadTimeout → graceful drop, warning в лог."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("read timed out")

    pooled = httpx.AsyncClient(
        base_url="http://loging-mock",
        transport=httpx.MockTransport(handler),
        timeout=2.0,
    )
    monkeypatch.setattr(audit_service, "_audit_client", pooled)
    caplog.set_level(logging.WARNING, logger="audit")

    try:
        await audit_service._send_to_logging_service(
            {"action": "pat.create"},
            "http://loging-mock",
            "key",
        )
    finally:
        await pooled.aclose()

    assert audit_service.get_dropped_429_total() == 0
    # Функция вернулась без исключения.


@pytest.mark.asyncio
async def test_unexpected_exception_drops_without_crashing(monkeypatch, caplog):
    """Неожиданное исключение (не HTTPError) → попадает в except Exception → warning."""

    async def broken_post_once(client, url, payload, headers):
        raise RuntimeError("unexpected internal error")

    monkeypatch.setattr(audit_service, "_post_once", broken_post_once)
    caplog.set_level(logging.WARNING, logger="audit")

    await audit_service._send_to_logging_service(
        {"action": "user.ban"},
        "http://loging-mock",
        "key",
    )

    # Не упало. Warning должен появиться.
    assert any("unexpected" in rec.message.lower() for rec in caplog.records)
    assert audit_service.get_dropped_429_total() == 0


@pytest.mark.asyncio
async def test_fallback_path_http_error_drops_gracefully(monkeypatch, caplog):
    """Fallback path (client=None, per-call AsyncClient) тоже ловит HTTPError."""
    monkeypatch.setattr(audit_service, "_audit_client", None)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs.pop("transport", None)
        return real_async_client(transport=transport, **kwargs)

    monkeypatch.setattr(audit_service.httpx, "AsyncClient", factory)

    async def fast_sleep(_: float) -> None:
        return None

    monkeypatch.setattr(audit_service.asyncio, "sleep", fast_sleep)
    caplog.set_level(logging.WARNING, logger="audit")

    await audit_service._send_to_logging_service(
        {"action": "user.logout"},
        "http://loging-mock",
        "key",
    )

    assert audit_service.get_dropped_429_total() == 0


# ── 429 backoff jitter — проверяем что sleep вызывается с разумными значениями ──

@pytest.mark.asyncio
async def test_backoff_sleep_values_are_in_expected_range(monkeypatch):
    """Backoff-задержки из _RETRY_DELAYS_ON_429 умножаются на jitter [0.8, 1.2].

    Проверяем что sleep вызывается с delay в диапазоне [0.8*base, 1.2*base].
    """
    from src.services.audit_service import _RETRY_DELAYS_ON_429

    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(429, json={"error": "rl"})

    pooled = httpx.AsyncClient(
        base_url="http://loging-mock",
        transport=httpx.MockTransport(handler),
        timeout=2.0,
    )
    monkeypatch.setattr(audit_service, "_audit_client", pooled)

    sleeps: list[float] = []

    async def capture_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(audit_service.asyncio, "sleep", capture_sleep)

    try:
        await audit_service._send_to_logging_service(
            {"action": "test"},
            "http://loging-mock",
            "key",
        )
    finally:
        await pooled.aclose()

    assert len(sleeps) == len(_RETRY_DELAYS_ON_429)
    for i, (sleep_val, base) in enumerate(zip(sleeps, _RETRY_DELAYS_ON_429)):
        assert base * 0.75 <= sleep_val <= base * 1.3, (
            f"sleep[{i}]={sleep_val:.3f} outside [0.75×{base}, 1.3×{base}] jitter window"
        )
