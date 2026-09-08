"""Retry-политика исходящего callback'а `prepare-for-test` в testing_service.

Потребителя ещё нет, поэтому важнее обычного, чтобы канал вёл себя
предсказуемо в трёх состояниях: не настроен, отвечает, отвечает плохо.
"""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from src.services import testing_client


def _settings(url: str = "http://testing-mock", key: str = "k") -> SimpleNamespace:
    return SimpleNamespace(
        testing_service_url=url,
        testing_service_api_key=key,
        testing_callback_timeout_seconds=2.0,
    )


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """Бэкоффы в тестах не ждём — проверяем счётчик попыток, не тайминги."""
    async def fake_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(testing_client, "_sleep", fake_sleep)


def _mock_client(monkeypatch, handler) -> None:
    def build(timeout: float) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.MockTransport(handler), timeout=timeout,
        )

    monkeypatch.setattr(testing_client, "build_client", build)


@pytest.mark.asyncio
async def test_skipped_when_channel_not_configured(monkeypatch):
    """Пустой URL/ключ — не сеть, а штатное «потребителя нет»: 0 попыток."""
    monkeypatch.setattr(
        testing_client, "get_settings", lambda: _settings(url="", key=""),
    )
    delivered, attempts, error = (
        await testing_client.send_prepare_for_test_completed("prep_1", {})
    )
    assert delivered is False
    assert attempts == 0
    assert error == "TESTING_SERVICE_NOT_CONFIGURED"


@pytest.mark.asyncio
async def test_delivered_carries_contract_path_and_identity(monkeypatch):
    """Успех с первой попытки; путь и заголовки — по контракту."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr(testing_client, "get_settings", lambda: _settings())
    _mock_client(monkeypatch, handler)

    delivered, attempts, error = (
        await testing_client.send_prepare_for_test_completed(
            "prep_abc", {"correlation_id": "qi_1", "succeeded": True},
        )
    )
    assert (delivered, attempts, error) == (True, 1, None)
    assert seen[0].url.path == "/internal/prepare-for-test/prep_abc/completed"
    assert seen[0].headers["authorization"] == "Bearer k"
    assert seen[0].headers["x-service-identity"] == "server_service"


@pytest.mark.asyncio
async def test_retries_on_503_then_gives_up(monkeypatch):
    """Три 503 подряд — три попытки и честное «не доставлено»."""
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(503)

    monkeypatch.setattr(testing_client, "get_settings", lambda: _settings())
    _mock_client(monkeypatch, handler)

    delivered, attempts, error = (
        await testing_client.send_prepare_for_test_completed("prep_1", {})
    )
    assert delivered is False
    assert attempts == 3
    assert calls["n"] == 3
    assert error == "HTTP 503"


@pytest.mark.asyncio
async def test_no_retry_on_contract_error(monkeypatch):
    """400 — ошибка контракта, а не недоступность: повторять нечего."""
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(400, json={"error": "bad_request"})

    monkeypatch.setattr(testing_client, "get_settings", lambda: _settings())
    _mock_client(monkeypatch, handler)

    delivered, attempts, error = (
        await testing_client.send_prepare_for_test_completed("prep_1", {})
    )
    assert delivered is False
    assert attempts == 1
    assert calls["n"] == 1
    assert error == "HTTP 400"
