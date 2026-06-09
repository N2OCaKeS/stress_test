"""Проверка JSON-логгера server_worker'а.

Содержательно: тест проверяет именно копию `src/core/logging.py`, а не
эталон в `sdk/`. Если оператор расходится с эталоном — здесь это всплывёт
по trivial-кейсам (формат полей, Bearer-redact, request_id из contextvar).
"""

from __future__ import annotations

import io
import json
import logging

import pytest

from src.core.logging import configure_logging, request_id_var


@pytest.fixture
def capture_stream():
    """Перенаправить вывод JSON-логгера в StringIO.

    Configure_logging кладёт StreamHandler на `sys.stdout`. pytest подменяет
    `sys.stdout` своим capture-stream'ом, поэтому monkeypatch на sys.stdout
    ненадёжен. Вместо этого: после вызова `configure_logging` в тесте мы
    перевешиваем `.stream` нашего корневого handler'а на свой StringIO.
    Это делается через хелпер `_redirect_root_handler(stream)`.

    Фикстура сохраняет root-handlers/level до теста и восстанавливает
    после — иначе следующий тест в suite'е писал бы в наш закрытый
    StringIO.
    """
    stream = io.StringIO()
    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    saved_level = root.level
    try:
        yield stream
    finally:
        for h in list(root.handlers):
            root.removeHandler(h)
        for h in saved_handlers:
            root.addHandler(h)
        root.setLevel(saved_level)


def _redirect_root_handler(stream: io.StringIO) -> None:
    """После `configure_logging(...)` развернуть единственный root-handler
    на наш StringIO. configure_logging навешивает РОВНО один StreamHandler
    (sys.stdout), и tests'ам удобнее перехватить его, чем гадать про
    pytest's capture wrapping."""
    root = logging.getLogger()
    assert len(root.handlers) == 1, f"expected 1 handler, got {root.handlers}"
    root.handlers[0].stream = stream


def _parse(stream: io.StringIO) -> dict:
    lines = [ln for ln in stream.getvalue().splitlines() if ln.strip()]
    assert lines, f"expected at least one log line, got: {stream.getvalue()!r}"
    return json.loads(lines[-1])


def test_json_format_baseline(capture_stream):
    configure_logging("server_worker", level="INFO")
    _redirect_root_handler(capture_stream)
    logging.getLogger("test").info("hello")
    payload = _parse(capture_stream)
    assert payload["service"] == "server_worker"
    assert payload["level"] == "INFO"
    assert payload["logger"] == "test"
    assert payload["message"] == "hello"
    assert "timestamp" in payload
    assert payload["timestamp"].endswith("Z")


def test_request_id_from_contextvar(capture_stream):
    configure_logging("server_worker", level="INFO")
    _redirect_root_handler(capture_stream)
    token = request_id_var.set("req_abc123")
    try:
        logging.getLogger("test").info("with rid")
    finally:
        request_id_var.reset(token)
    payload = _parse(capture_stream)
    assert payload["request_id"] == "req_abc123"


def test_request_id_absent_when_unset(capture_stream):
    configure_logging("server_worker", level="INFO")
    _redirect_root_handler(capture_stream)
    logging.getLogger("test").info("no rid")
    payload = _parse(capture_stream)
    assert "request_id" not in payload


def test_bearer_token_redacted_in_message(capture_stream):
    configure_logging("server_worker", level="INFO")
    _redirect_root_handler(capture_stream)
    logging.getLogger("test").info(
        "request headers: Authorization: Bearer eyJabc.def.ghi"
    )
    payload = _parse(capture_stream)
    assert "eyJabc.def.ghi" not in payload["message"]
    assert "<REDACTED>" in payload["message"]


def test_bearer_token_redacted_in_exception(capture_stream):
    configure_logging("server_worker", level="INFO")
    _redirect_root_handler(capture_stream)
    try:
        raise RuntimeError("upstream returned Bearer secrettoken123")
    except RuntimeError:
        logging.getLogger("test").exception("upstream failed")
    payload = _parse(capture_stream)
    assert "secrettoken123" not in payload.get("exception", "")
    assert "secrettoken123" not in payload["message"]


def test_httpx_logger_clamped_to_warning(capture_stream):
    configure_logging("server_worker", level="DEBUG")
    _redirect_root_handler(capture_stream)
    # На DEBUG-уровне корня httpx всё равно должен быть WARNING — иначе
    # httpx._client пишет полные headers с Authorization: Bearer ... в stdout.
    httpx_logger = logging.getLogger("httpx")
    assert httpx_logger.level == logging.WARNING
    httpx_logger.debug("would leak Bearer eyJsecret.payload.signature")
    httpx_logger.info("would also leak")
    # Ни DEBUG, ни INFO не должны попасть в stream при clamped WARNING.
    assert "eyJsecret" not in capture_stream.getvalue()


def test_extra_fields_merged(capture_stream):
    configure_logging("server_worker", level="INFO")
    _redirect_root_handler(capture_stream)
    logging.getLogger("test").info("ok", extra={"user_id": "42", "action": "login"})
    payload = _parse(capture_stream)
    assert payload["user_id"] == "42"
    assert payload["action"] == "login"


def test_reserved_keys_in_extra_quarantined(capture_stream):
    configure_logging("server_worker", level="INFO")
    _redirect_root_handler(capture_stream)
    # Если пользователь случайно подставил `service`/`logger` через extra —
    # обязательные поля не должны перезаписаться.
    logging.getLogger("test").info(
        "ok", extra={"service": "spoofed", "custom": "ok"}
    )
    payload = _parse(capture_stream)
    assert payload["service"] == "server_worker"
    assert payload["custom"] == "ok"
    assert payload.get("_extra", {}).get("service") == "spoofed"
