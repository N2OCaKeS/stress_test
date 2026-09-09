"""JSON-структурированное логирование.

# SOURCE OF TRUTH: dbos_server_service/sdk/logging.py
# Копия того же модуля, что уже стоит в auth_service/loging_service/
# server_service/secret_service/server_worker — общего импорта между
# сервисами нет (изолированный PYTHONPATH), поэтому каждый держит свою копию.
# При правке в одном месте — синхронизировать руками во все шесть.

Контракт:
  - JSON-line на stdout (один объект на строку);
  - обязательные поля: `timestamp` (ISO8601 UTC), `level`, `service`,
    `logger`, `message`;
  - `request_id` — из contextvar `request_id_var`, если установлен middleware'ом;
  - extra-поля мерджатся в корень JSON, кроме reserved keys;
  - Bearer-токены маскируются regex'ом в `message`/`exception`, httpx/httpcore/
    hpack/urllib3 глушатся до WARNING (DEBUG утекает Authorization-header).

Использование в `main.py`/`worker_main.py`:

    from src.core.logging import configure_logging, request_id_var
    configure_logging("testing_service", level=settings.app_log_level)
"""

from __future__ import annotations

import json
import logging
import re
import sys
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

_RESERVED_FIELDS = frozenset(
    {"timestamp", "level", "service", "logger", "message", "exception", "request_id"}
)

_DEFAULT_QUIET_LOGGERS: tuple[str, ...] = (
    "httpx",
    "httpcore",
    "hpack",
    "urllib3",
)

_BEARER_RE = re.compile(
    r"(?i)(authorization\s*[:=]\s*)?bearer\s+[A-Za-z0-9_\-\.]+",
)


def _redact_bearer(text: str) -> str:
    """Замаскировать Bearer-токены в свободной строке."""
    return _BEARER_RE.sub("Authorization: Bearer <REDACTED>", text)


class _JsonFormatter(logging.Formatter):
    """Formatter, превращающий LogRecord в одну JSON-строку."""

    def __init__(self, service_name: str) -> None:
        super().__init__()
        self._service = service_name

    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat()
        if ts.endswith("+00:00"):
            ts = ts[:-6] + "Z"

        message = _redact_bearer(record.getMessage())

        payload: dict[str, Any] = {
            "timestamp": ts,
            "level": record.levelname,
            "service": self._service,
            "logger": record.name,
            "message": message,
        }

        rid = request_id_var.get()
        if rid:
            payload["request_id"] = rid

        if record.exc_info:
            payload["exception"] = _redact_bearer(self.formatException(record.exc_info))

        for key, value in record.__dict__.items():
            if key in _LOG_RECORD_BUILTIN_ATTRS:
                continue
            if key in _RESERVED_FIELDS:
                payload.setdefault("_extra", {})[key] = value
                continue
            payload[key] = value

        return json.dumps(payload, ensure_ascii=False, default=str)


_LOG_RECORD_BUILTIN_ATTRS = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "message",
        "module",
        "msecs",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "thread",
        "threadName",
        "taskName",
    }
)


def configure_logging(
    service_name: str,
    level: str = "INFO",
    *,
    json_format: bool = True,
    quiet_loggers: tuple[str, ...] = _DEFAULT_QUIET_LOGGERS,
) -> None:
    """Единая точка настройки логирования. Идемпотентна."""
    root = logging.getLogger()

    for h in list(root.handlers):
        root.removeHandler(h)

    handler = logging.StreamHandler(stream=sys.stdout)
    if json_format:
        handler.setFormatter(_JsonFormatter(service_name))
    else:
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
                datefmt="%Y-%m-%dT%H:%M:%S",
            )
        )

    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    for name in quiet_loggers:
        logging.getLogger(name).setLevel(logging.WARNING)
