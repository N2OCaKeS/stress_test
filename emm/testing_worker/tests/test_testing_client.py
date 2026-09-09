"""Тесты `services/testing_client.py` — claim/completed без реального testing_service.

`build_client` подменяется на `httpx.MockTransport`, тем же приёмом, что и
`server_client`-мок в `testing_service/tests/test_queue.py`.
"""

from __future__ import annotations

import httpx
import pytest

from src.core.config import get_settings
from src.services import testing_client

WORKER_SECRET = "test-testing-worker-secret"


@pytest.fixture(autouse=True)
def _configure(monkeypatch):
    monkeypatch.setenv("TESTING_SERVICE_URL", "http://testing-service")
    monkeypatch.setenv("TESTING_SERVICE_INTERNAL_API_KEY", WORKER_SECRET)
    get_settings.cache_clear()  # type: ignore[attr-defined]
    yield
    get_settings.cache_clear()  # type: ignore[attr-defined]


def _install_transport(monkeypatch, handler):
    def _build(timeout: float) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(testing_client, "build_client", _build)


class TestClaim:
    async def test_returns_item_on_success(self, monkeypatch):
        recorded = {}

        def handler(request: httpx.Request) -> httpx.Response:
            recorded["path"] = request.url.path
            recorded["auth"] = request.headers.get("Authorization")
            recorded["identity"] = request.headers.get("X-Service-Identity")
            return httpx.Response(200, json={"item": {"queue_item_id": "qi_1", "host": "10.0.0.1"}})

        _install_transport(monkeypatch, handler)

        item = await testing_client.claim()

        assert item == {"queue_item_id": "qi_1", "host": "10.0.0.1"}
        assert recorded["path"] == "/internal/queue/claim"
        assert recorded["auth"] == f"Bearer {WORKER_SECRET}"
        assert recorded["identity"] == "testing_worker"

    async def test_returns_none_when_queue_empty(self, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"item": None})

        _install_transport(monkeypatch, handler)

        assert await testing_client.claim() is None

    async def test_returns_none_on_network_failure(self, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused", request=request)

        _install_transport(monkeypatch, handler)

        # Не должно поднимать исключение наружу — polling loop продолжает жить.
        assert await testing_client.claim() is None

    async def test_returns_none_on_non_200(self, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, json={"error": "boom"})

        _install_transport(monkeypatch, handler)

        assert await testing_client.claim() is None

    async def test_returns_none_when_not_configured(self, monkeypatch):
        monkeypatch.delenv("TESTING_SERVICE_INTERNAL_API_KEY", raising=False)
        get_settings.cache_clear()  # type: ignore[attr-defined]

        assert await testing_client.claim() is None


class TestReportCompleted:
    async def test_posts_expected_body(self, monkeypatch):
        recorded = {}

        def handler(request: httpx.Request) -> httpx.Response:
            recorded["path"] = request.url.path
            recorded["body"] = request.content
            recorded["identity"] = request.headers.get("X-Service-Identity")
            return httpx.Response(200, json={"ok": True})

        _install_transport(monkeypatch, handler)

        await testing_client.report_completed(
            "qi_1", succeeded=True, exit_code=0, error=None,
        )

        assert recorded["path"] == "/internal/queue/qi_1/completed"
        assert recorded["identity"] == "testing_worker"
        import json as _json

        body = _json.loads(recorded["body"])
        assert body == {"succeeded": True, "exit_code": 0, "error": None}

    async def test_does_not_raise_on_network_failure(self, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused", request=request)

        _install_transport(monkeypatch, handler)

        # No exception expected.
        await testing_client.report_completed(
            "qi_1", succeeded=False, exit_code=None, error="boom",
        )

    async def test_does_not_raise_on_non_200(self, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, json={})

        _install_transport(monkeypatch, handler)

        await testing_client.report_completed(
            "qi_missing", succeeded=True, exit_code=0, error=None,
        )
