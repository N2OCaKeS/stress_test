"""Тесты `services/testing_client.py` — claim/completed без реального testing_service.

`build_client` подменяется на `httpx.MockTransport`, тем же приёмом, что и
`server_client`-мок в `testing_service/tests/test_queue.py`.
"""

from __future__ import annotations

import json as _json
from datetime import datetime, timezone

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
    testing_client.reset_client()


_VALID_CLAIM_ITEM = {
    "queue_item_id": "qi_1",
    "host": "10.0.0.1",
    "test_username": "u",
    "test_ssh_private_key": "keydata",
    "command": ["sudo", "bash", "/home/u/starter.sh"],
    "command_masked": ["sudo", "bash", "/home/u/starter.sh"],
    "dates_content": "-sn 1",
    "dates_content_masked": "-sn 1",
    "dates_filename": "dates_qi_1.conf",
    "debug_mode": False,
    "is_retry": False,
}


class TestClaim:
    async def test_returns_item_on_success(self, monkeypatch):
        recorded = {}

        def handler(request: httpx.Request) -> httpx.Response:
            recorded["path"] = request.url.path
            recorded["auth"] = request.headers.get("Authorization")
            recorded["identity"] = request.headers.get("X-Service-Identity")
            return httpx.Response(200, json={"item": _VALID_CLAIM_ITEM})

        _install_transport(monkeypatch, handler)

        item = await testing_client.claim()

        assert item is not None
        assert item["queue_item_id"] == "qi_1"
        assert item["host"] == "10.0.0.1"
        assert item["command"] == ["sudo", "bash", "/home/u/starter.sh"]
        assert recorded["path"] == "/internal/queue/claim"
        assert recorded["auth"] == f"Bearer {WORKER_SECRET}"
        assert recorded["identity"] == "testing_worker"

    async def test_unknown_fields_are_dropped_not_fatal(self, monkeypatch):
        """Лишнее поле в ответе (например, устаревшее `test_password`) не повод падать."""
        item_with_extra = {**_VALID_CLAIM_ITEM, "test_password": "s3cr3t", "surprise_field": 123}

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"item": item_with_extra})

        _install_transport(monkeypatch, handler)

        item = await testing_client.claim()

        assert item is not None
        assert "test_password" not in item
        assert "surprise_field" not in item

    async def test_missing_required_field_reports_completed_and_returns_none(self, monkeypatch):
        """Рассинхрон контракта (нет `test_ssh_private_key`) — явный провал item'а,
        а не голый `KeyError` где-то посреди `_run_one_item`."""
        recorded = {"calls": []}
        broken_item = {k: v for k, v in _VALID_CLAIM_ITEM.items() if k != "test_ssh_private_key"}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/internal/queue/claim":
                return httpx.Response(200, json={"item": broken_item})
            recorded["calls"].append((request.url.path, _json.loads(request.content)))
            return httpx.Response(200, json={"ok": True})

        _install_transport(monkeypatch, handler)

        item = await testing_client.claim()

        assert item is None
        assert len(recorded["calls"]) == 1
        path, body = recorded["calls"][0]
        assert path == "/internal/queue/qi_1/completed"
        assert body["succeeded"] is False
        assert "malformed claim response" in body["error"]

    async def test_missing_queue_item_id_cannot_report_but_does_not_crash(self, monkeypatch):
        """Без `queue_item_id` некому отчитаться — просто честно теряем item,
        не поднимая исключение наружу."""
        broken_item = {k: v for k, v in _VALID_CLAIM_ITEM.items() if k != "queue_item_id"}
        calls = {"completed": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/internal/queue/claim":
                return httpx.Response(200, json={"item": broken_item})
            calls["completed"] += 1
            return httpx.Response(200, json={"ok": True})

        _install_transport(monkeypatch, handler)

        assert await testing_client.claim() is None
        assert calls["completed"] == 0

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
        body = _json.loads(recorded["body"])
        assert body == {"succeeded": True, "exit_code": 0, "error": None, "interrupted": None}

    async def test_interrupted_run_is_reported_as_such(self, monkeypatch):
        recorded = {}

        def handler(request: httpx.Request) -> httpx.Response:
            recorded["body"] = request.content
            return httpx.Response(200, json={"ok": True})

        _install_transport(monkeypatch, handler)

        await testing_client.report_completed(
            "qi_1", succeeded=False, exit_code=None, error=None, interrupted="pause",
        )

        assert _json.loads(recorded["body"])["interrupted"] == "pause"

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


class TestCheckInterrupt:
    @pytest.mark.parametrize("action", ["skip", "pause"])
    async def test_returns_requested_action(self, monkeypatch, action):
        recorded = {}

        def handler(request: httpx.Request) -> httpx.Response:
            recorded["method"] = request.method
            recorded["path"] = request.url.path
            recorded["identity"] = request.headers.get("X-Service-Identity")
            return httpx.Response(200, json={"action": action})

        _install_transport(monkeypatch, handler)

        assert await testing_client.check_interrupt("qi_1") == action
        assert recorded["method"] == "GET"
        assert recorded["path"] == "/internal/queue/qi_1/interrupt-check"
        assert recorded["identity"] == "testing_worker"

    async def test_null_action_means_keep_running(self, monkeypatch):
        _install_transport(monkeypatch, lambda request: httpx.Response(200, json={"action": None}))

        assert await testing_client.check_interrupt("qi_1") is None

    async def test_unknown_action_is_ignored(self, monkeypatch):
        """Только `skip`/`pause` считаются командой — иначе тест не обрываем."""
        _install_transport(monkeypatch, lambda request: httpx.Response(200, json={"action": "explode"}))

        assert await testing_client.check_interrupt("qi_1") is None

    async def test_returns_none_on_network_failure(self, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused", request=request)

        _install_transport(monkeypatch, handler)

        assert await testing_client.check_interrupt("qi_1") is None

    async def test_returns_none_on_non_200(self, monkeypatch):
        _install_transport(monkeypatch, lambda request: httpx.Response(500, json={}))

        assert await testing_client.check_interrupt("qi_1") is None

    async def test_returns_none_when_not_configured(self, monkeypatch):
        monkeypatch.delenv("TESTING_SERVICE_URL", raising=False)
        get_settings.cache_clear()  # type: ignore[attr-defined]

        assert await testing_client.check_interrupt("qi_1") is None


class TestLogChunk:
    async def test_posts_expected_body(self, monkeypatch):
        recorded = {}

        def handler(request: httpx.Request) -> httpx.Response:
            recorded["path"] = request.url.path
            recorded["body"] = request.content
            recorded["identity"] = request.headers.get("X-Service-Identity")
            return httpx.Response(200, json={"ok": True, "log_id": "tlog_1"})

        _install_transport(monkeypatch, handler)

        await testing_client.log_chunk("qi_1", "some incremental output")

        assert recorded["path"] == "/internal/queue/qi_1/log-chunk"
        assert recorded["identity"] == "testing_worker"
        body = _json.loads(recorded["body"])
        assert body == {"text": "some incremental output"}

    async def test_empty_text_does_not_call_out(self, monkeypatch):
        called = {"count": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            called["count"] += 1
            return httpx.Response(200, json={"ok": True, "log_id": "tlog_1"})

        _install_transport(monkeypatch, handler)

        await testing_client.log_chunk("qi_1", "")

        assert called["count"] == 0

    async def test_does_not_raise_on_network_failure(self, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused", request=request)

        _install_transport(monkeypatch, handler)

        await testing_client.log_chunk("qi_1", "text")

    async def test_does_not_raise_on_non_200(self, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, json={"error": "boom"})

        _install_transport(monkeypatch, handler)

        await testing_client.log_chunk("qi_1", "text")

    async def test_does_not_raise_when_not_configured(self, monkeypatch):
        monkeypatch.delenv("TESTING_SERVICE_INTERNAL_API_KEY", raising=False)
        get_settings.cache_clear()  # type: ignore[attr-defined]

        await testing_client.log_chunk("qi_1", "text")


class TestLogSegment:
    _STARTED = datetime(2026, 9, 9, 12, 0, 0, tzinfo=timezone.utc)
    _FINISHED = datetime(2026, 9, 9, 12, 0, 5, tzinfo=timezone.utc)

    async def test_posts_expected_body(self, monkeypatch):
        recorded = {}

        def handler(request: httpx.Request) -> httpx.Response:
            recorded["path"] = request.url.path
            recorded["body"] = request.content
            recorded["identity"] = request.headers.get("X-Service-Identity")
            return httpx.Response(200, json={"ok": True, "log_id": "tlog_1"})

        _install_transport(monkeypatch, handler)

        await testing_client.log_segment(
            "qi_1",
            kind="command",
            label="Выполнение теста",
            status="OK",
            command_text_masked="run.py --password ***",
            output="all good",
            host="10.0.0.1",
            started_at=self._STARTED,
            finished_at=self._FINISHED,
        )

        assert recorded["path"] == "/internal/queue/qi_1/log-segment"
        assert recorded["identity"] == "testing_worker"
        body = _json.loads(recorded["body"])
        assert body == {
            "kind": "command",
            "label": "Выполнение теста",
            "status": "OK",
            "command_text_masked": "run.py --password ***",
            "output": "all good",
            "host": "10.0.0.1",
            "started_at": self._STARTED.isoformat(),
            "finished_at": self._FINISHED.isoformat(),
        }

    async def test_does_not_raise_on_network_failure(self, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused", request=request)

        _install_transport(monkeypatch, handler)

        await testing_client.log_segment(
            "qi_1", kind="command", label="l", status="FATAL",
            command_text_masked=None, output="", host="10.0.0.1",
            started_at=self._STARTED, finished_at=self._FINISHED,
        )

    async def test_does_not_raise_on_non_200(self, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, json={})

        _install_transport(monkeypatch, handler)

        await testing_client.log_segment(
            "qi_1", kind="command", label="l", status="OK",
            command_text_masked=None, output="", host="10.0.0.1",
            started_at=self._STARTED, finished_at=self._FINISHED,
        )

    async def test_does_not_raise_when_not_configured(self, monkeypatch):
        monkeypatch.delenv("TESTING_SERVICE_INTERNAL_API_KEY", raising=False)
        get_settings.cache_clear()  # type: ignore[attr-defined]

        await testing_client.log_segment(
            "qi_1", kind="command", label="l", status="OK",
            command_text_masked=None, output="", host="10.0.0.1",
            started_at=self._STARTED, finished_at=self._FINISHED,
        )


class TestSharedClient:
    async def test_one_client_serves_consecutive_calls(self, monkeypatch):
        built = []

        def _build(timeout: float) -> httpx.AsyncClient:
            client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"item": None})))
            built.append(client)
            return client

        monkeypatch.setattr(testing_client, "build_client", _build)
        testing_client.reset_client()

        await testing_client.claim()
        await testing_client.claim()

        assert len(built) == 1
        await testing_client.aclose()
        assert built[0].is_closed

    async def test_client_is_rebuilt_after_close(self, monkeypatch):
        built = []

        def _build(timeout: float) -> httpx.AsyncClient:
            client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"item": None})))
            built.append(client)
            return client

        monkeypatch.setattr(testing_client, "build_client", _build)
        testing_client.reset_client()

        await testing_client.claim()
        await testing_client.aclose()
        await testing_client.claim()

        assert len(built) == 2
        await testing_client.aclose()
