"""Unit-тесты `src/services/audit_client.py`.

Покрытие:
* payload содержит обязательные поля (`action`, `status`, `allowed`,
  `actor_type=service`, `service=server_worker`, `timestamp` ISO);
* опциональные поля попадают только когда переданы;
* `Authorization: Bearer <api_key>` ставится из `logging_service_api_key`
  (никакого fallback'а на `worker_bot_token` — это PAT для server_service
  и его использование тут означало бы token reuse / порчу audit trail);
* при пустом `logging_service_api_key` — event дропается, HTTP не идёт,
  логируется `error` (это единственная ветка graceful-swallow: без ключа
  retry бесполезен);
* httpx.HTTPError (timeout, connect) → `AuditEmitError` (raise);
* 4xx/5xx → `AuditEmitError` (raise).

«Fail loud»-семантика (фикс «emit swallow 4xx/5xx →
published-but-not-delivered»): до этого emit
логировал warning и тихо возвращал None — outbox publisher принимал это
за успех и помечал row published, что приводило к silent loss audit-event.

С переходом на pooled httpx-клиент тесты подменяют возвращаемый
`get_audit_client()` объект — поведение `post()` под нагрузкой имитируется
прямо в этом mock'е.
"""

from __future__ import annotations

from datetime import datetime

import httpx
import pytest

from src.services import audit_client
from src.services import http_pool
from src.services.audit_client import AuditEmitError


class _MockResponse:
    def __init__(self, status_code: int = 201):
        self.status_code = status_code


class _PoolClientCapture:
    """Подмена pooled `httpx.AsyncClient` — захватывает аргументы post()."""

    def __init__(self):
        self.captured: list[dict] = []

    async def post(self, url, json=None, headers=None):
        self.captured.append({"url": url, "json": json, "headers": headers or {}})
        return _MockResponse(201)


@pytest.fixture(autouse=True)
def _reset_http_pool():
    """Сбрасываем pool-кеш до и после каждого теста.

    Без этого закешированный реальный AsyncClient переживёт monkeypatch
    в соседнем тесте.
    """
    http_pool.reset_for_tests()
    yield
    http_pool.reset_for_tests()


@pytest.fixture
def settings_stub(monkeypatch):
    """Подменяет get_settings + pool-getter под захватывающий клиент."""

    class _S:
        logging_service_url = "http://logging.test"
        logging_service_api_key = "k-sample"
        worker_bot_token = "wbt-sample"
        http_request_timeout_seconds = 5.0

    capture = _PoolClientCapture()
    monkeypatch.setattr("src.services.audit_client.get_settings", lambda: _S())
    monkeypatch.setattr(
        "src.services.audit_client.get_audit_client", lambda: capture,
    )
    return capture


# ── Payload composition ──────────────────────────────────────────────────────

class TestAuditPayload:
    async def test_minimal_call_has_required_fields(self, settings_stub):
        await audit_client.emit("power.success")
        assert settings_stub.captured
        payload = settings_stub.captured[0]["json"]
        assert payload["action"] == "power.success"
        assert payload["status"] == "success"
        assert payload["allowed"] is True
        assert payload["actor_type"] == "service"
        assert payload["service"] == "server_worker"
        # timestamp — валидный ISO 8601 с tz
        datetime.fromisoformat(payload["timestamp"])

    async def test_optional_fields_only_when_provided(self, settings_stub):
        await audit_client.emit(
            "x.y",
            actor_id="bot_1", department_id="dep_a",
            target_id="srv_1", target_type="server",
            severity="WARNING", request_id="req_1",
            details={"k": "v"},
        )
        payload = settings_stub.captured[0]["json"]
        assert payload["actor_id"] == "bot_1"
        assert payload["department_id"] == "dep_a"
        assert payload["target_id"] == "srv_1"
        assert payload["target_type"] == "server"
        assert payload["severity"] == "WARNING"
        assert payload["request_id"] == "req_1"
        assert payload["details"] == {"k": "v"}

    async def test_omitted_fields_absent(self, settings_stub):
        await audit_client.emit("x.y")
        payload = settings_stub.captured[0]["json"]
        for f in ("actor_id", "department_id", "target_id", "target_type",
                  "severity", "request_id", "details"):
            assert f not in payload, f"{f} must not be present when not passed"

    async def test_empty_details_not_attached(self, settings_stub):
        """`details={}` — falsy → не добавляется."""
        await audit_client.emit("x.y", details={})
        payload = settings_stub.captured[0]["json"]
        assert "details" not in payload

    async def test_explicit_timestamp_kwarg_preserved(self, settings_stub):
        """Если caller передал timestamp — он попадает в payload как есть.

        Сценарий: publisher разворачивает outbox-row.payload как kwargs;
        timestamp положен `enqueue_audit` в момент enqueue (event-time).
        Если loging лежал и publisher довозит через час, audit указывает
        реальное время события, а не публикации.
        """
        event_time = "2025-01-15T10:00:00+00:00"
        await audit_client.emit("x.y", timestamp=event_time)
        payload = settings_stub.captured[0]["json"]
        assert payload["timestamp"] == event_time

    async def test_no_timestamp_kwarg_falls_back_to_now(self, settings_stub):
        """Без timestamp — берём now() (legacy путь, прямой emit без outbox)."""
        await audit_client.emit("x.y")
        payload = settings_stub.captured[0]["json"]
        datetime.fromisoformat(payload["timestamp"])


# ── Auth header ──────────────────────────────────────────────────────────────

class TestAuthHeader:
    async def test_api_key_used_when_set(self, settings_stub):
        await audit_client.emit("x.y")
        headers = settings_stub.captured[0]["headers"]
        assert headers["Authorization"] == "Bearer k-sample"

    async def test_empty_api_key_drops_event_no_http(self, monkeypatch, caplog):
        """`logging_service_api_key=""` → нет HTTP запроса, error в логе.

        Главное: PAT (`worker_bot_token`) НЕ должен использоваться даже если
        он задан — иначе можно подделать audit-события с любым `service=`.
        """
        class _S:
            logging_service_url = "http://logging.test"
            logging_service_api_key = ""
            worker_bot_token = "wbt-MUST-NOT-LEAK"
            http_request_timeout_seconds = 5.0

        capture = _PoolClientCapture()
        monkeypatch.setattr("src.services.audit_client.get_settings", lambda: _S())
        monkeypatch.setattr(
            "src.services.audit_client.get_audit_client", lambda: capture,
        )

        with caplog.at_level("ERROR", logger="src.services.audit_client"):
            with pytest.raises(audit_client.AuditEmitError, match="LOGGING_SERVICE_API_KEY"):
                await audit_client.emit("x.y")

        # ни одного HTTP-запроса не должно быть
        assert capture.captured == []
        # error залогирован
        assert any(
            "LOGGING_SERVICE_API_KEY" in r.message and r.levelname == "ERROR"
            for r in caplog.records
        )

    async def test_both_keys_empty_drops_event_gracefully(self, monkeypatch, caplog):
        """Оба ключа пусты — raise AuditEmitError (publisher уведёт в DLQ)."""
        class _S:
            logging_service_url = "http://logging.test"
            logging_service_api_key = ""
            worker_bot_token = ""
            http_request_timeout_seconds = 5.0

        capture = _PoolClientCapture()
        monkeypatch.setattr("src.services.audit_client.get_settings", lambda: _S())
        monkeypatch.setattr(
            "src.services.audit_client.get_audit_client", lambda: capture,
        )

        with caplog.at_level("ERROR", logger="src.services.audit_client"):
            with pytest.raises(audit_client.AuditEmitError):
                await audit_client.emit("x.y")

        assert capture.captured == []
        assert any("LOGGING_SERVICE_API_KEY" in r.message for r in caplog.records)


# ── URL composition ──────────────────────────────────────────────────────────

class TestUrl:
    async def test_url_includes_ingest_path(self, settings_stub):
        await audit_client.emit("x.y")
        assert settings_stub.captured[0]["url"] == "http://logging.test/api/logging/v1/events"

    async def test_trailing_slash_in_base_stripped(self, monkeypatch):
        class _S:
            logging_service_url = "http://logging.test/"
            logging_service_api_key = "k"
            worker_bot_token = ""
            http_request_timeout_seconds = 5.0

        capture = _PoolClientCapture()
        monkeypatch.setattr("src.services.audit_client.get_settings", lambda: _S())
        monkeypatch.setattr(
            "src.services.audit_client.get_audit_client", lambda: capture,
        )
        await audit_client.emit("x.y")
        assert capture.captured[0]["url"] == "http://logging.test/api/logging/v1/events"


# ── Fail-loud на HTTP ошибках ────────────────────────────────────────────────
#
# Контракт: emit raise'ит `AuditEmitError` на любой HTTP-фейл (4xx/5xx) и
# на транспортные исключения httpx. До этого emit глотал ошибки и возвращал
# None — outbox publisher принимал это за успех и терял audit-событие.

def _settings_default() -> type:
    class _S:
        logging_service_url = "http://logging.test"
        logging_service_api_key = "k"
        worker_bot_token = ""
        http_request_timeout_seconds = 5.0
    return _S


def _install_pool_with_post(monkeypatch, post_impl):
    """Поднять fake pool-client, у которого `post()` делает то, что хотим.

    `post_impl` — async-функция `(url, json, headers) -> response | raise`.
    """
    class _FakePoolClient:
        async def post(self, url, json=None, headers=None):
            return await post_impl(url, json, headers)

    fake = _FakePoolClient()
    monkeypatch.setattr(
        "src.services.audit_client.get_settings", lambda: _settings_default()(),
    )
    monkeypatch.setattr(
        "src.services.audit_client.get_audit_client", lambda: fake,
    )


class TestRaiseOnHttpFailure:
    async def test_4xx_raises_audit_emit_error(self, monkeypatch, caplog):
        async def _post(url, json, headers):
            return _MockResponse(403)

        _install_pool_with_post(monkeypatch, _post)
        with pytest.raises(AuditEmitError) as exc_info:
            await audit_client.emit("x.y")
        # error_message содержит HTTP-код и action — нужно для диагностики
        # в `audit_outbox.last_error`.
        assert "403" in exc_info.value.error_message
        assert "x.y" in exc_info.value.error_message
        # status_code заполнен — publisher классифицирует 4xx как permanent.
        assert exc_info.value.status_code == 403

    async def test_422_raises_audit_emit_error(self, monkeypatch):
        """422 (schema validation) — raise со `status_code=422`. Publisher
        теперь классифицирует 4xx как permanent-fatal: row пойдёт в DLQ
        сразу, без накручивания attempts (см. `_publish_one`)."""
        async def _post(url, json, headers):
            return _MockResponse(422)

        _install_pool_with_post(monkeypatch, _post)
        with pytest.raises(AuditEmitError) as exc_info:
            await audit_client.emit("x.y")
        assert exc_info.value.status_code == 422

    async def test_500_raises_audit_emit_error(self, monkeypatch):
        async def _post(url, json, headers):
            return _MockResponse(500)

        _install_pool_with_post(monkeypatch, _post)
        with pytest.raises(AuditEmitError) as exc_info:
            await audit_client.emit("x.y")
        assert "500" in exc_info.value.error_message
        # 5xx → status_code заполнен, но publisher классифицирует это как
        # transient (retry через outbox).
        assert exc_info.value.status_code == 500

    async def test_503_raises_audit_emit_error(self, monkeypatch):
        async def _post(url, json, headers):
            return _MockResponse(503)

        _install_pool_with_post(monkeypatch, _post)
        with pytest.raises(AuditEmitError):
            await audit_client.emit("x.y")

    async def test_timeout_raises_audit_emit_error(self, monkeypatch):
        async def _post(url, json, headers):
            raise httpx.TimeoutException("slow")

        _install_pool_with_post(monkeypatch, _post)
        with pytest.raises(AuditEmitError) as exc_info:
            await audit_client.emit("x.y")
        # Имя оригинального класса — нужно для диагностики в outbox.last_error.
        assert "Timeout" in exc_info.value.error_message
        # Transport-уровень — status_code отсутствует. Publisher
        # классифицирует None как transient (retry).
        assert exc_info.value.status_code is None

    async def test_connect_error_raises_audit_emit_error(self, monkeypatch):
        async def _post(url, json, headers):
            raise httpx.ConnectError("down")

        _install_pool_with_post(monkeypatch, _post)
        with pytest.raises(AuditEmitError) as exc_info:
            await audit_client.emit("x.y")
        assert "ConnectError" in exc_info.value.error_message
        # Transport-уровень — status_code отсутствует.
        assert exc_info.value.status_code is None

    async def test_2xx_does_not_raise(self, settings_stub):
        """Happy-path: успешный POST → нет исключения, нет return value."""
        result = await audit_client.emit("x.y")
        assert result is None
        # И HTTP всё-таки выполнен.
        assert len(settings_stub.captured) == 1
