"""JSON-структурированное логирование — эталонный источник для копи-паста.

# SOURCE OF TRUTH: dbos_server_service/sdk/logging.py
# DUPE: keep in sync with
#   - auth_service/src/core/logging.py
#   - loging_service/src/core/logging.py
#   - server_service/src/core/logging.py
#   - secret_service/src/core/logging.py
#   - server_worker/src/core/logging.py
# Каталог `sdk/` — не pip-пакет, а справочный источник. PYTHONPATH сервисов
# изолирован; общего импорта между ними нет. Каждый сервис держит свою копию
# этого файла; при правке здесь — синхронизируй вручную во все пять мест.
#
# Зачем общий модуль: до этого 5 сервисов писали в 5 разных форматах
# (`%(asctime)s | %(levelname)s | %(name)s | %(message)s`,
#  `%(asctime)s [%(levelname)s] ...`, `%(asctime)s %(levelname)s ...`),
# секрет/server вообще не звали `configure_logging()`. Grep по логам в
# инцидентах не сходился: разные ключи, разный таймстемп, request_id терялся
# при переходе HTTP-границы.
#
# Контракт:
#   - JSON-line на stdout (один объект на строку, без trailing-newlines внутри);
#   - обязательные поля: `timestamp` (ISO8601 UTC), `level`, `service`,
#     `logger`, `message`;
#   - `request_id` — берётся из contextvar `request_id_var`, если установлен;
#     должен подцепляться middleware'ом при `X-Request-ID` propagation.
#   - extra-поля (через `logger.info("msg", extra={"foo": 1})`) — мерджатся
#     в корень JSON. Reserved keys (`timestamp`/`level`/`service`/`logger`/
#     `message`/`exception`/`request_id`) не перезаписываются.
#   - значения через `redact()` НЕ прогоняются на уровне formatter'а —
#     это слишком дорого для каждого лог-вызова. Вместо этого мы:
#       (a) глушим `httpx`/`httpcore`/`hpack` до WARNING (DEBUG-уровень
#           утечёт `Authorization: Bearer ...` в лог),
#       (b) regex'ом маскируем `Authorization: Bearer <…>` / `bearer <…>`
#           в `message` и `exception` — на случай, если кто-то залогирует
#           сырой header сам.
#
# Использование в `main.py`:
#
#     from src.core.logging import configure_logging, request_id_var
#     configure_logging("auth_service", level=settings.app_log_level)
#     # дальше — обычный logging.getLogger(__name__).info(...)
"""

from __future__ import annotations

import json
import logging
import re
import sys
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any

# Глобальный контекст для request_id — заполняется middleware'ом в
# attach_request_id (см. `main.py` каждого сервиса). Если не выставлен —
# поле `request_id` не попадает в JSON.
request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)


# Reserved keys, которые extra НЕ может перезаписать. Эти поля формирует сам
# formatter; если они придут через extra, они уйдут в `_extra` (см. ниже).
_RESERVED_FIELDS = frozenset(
    {"timestamp", "level", "service", "logger", "message", "exception", "request_id"}
)

# Шумные HTTP-логгеры. На DEBUG-уровне `httpx._client` пишет полный
# `Request headers: {..., 'authorization': 'Bearer ey...'}` — это утечка в
# журнал. Жёстко загоняем в WARNING (override-able через configure_logging
# параметром `quiet_loggers`).
_DEFAULT_QUIET_LOGGERS: tuple[str, ...] = (
    "httpx",
    "httpcore",
    "hpack",
    "urllib3",
)

# Regex для маскировки Bearer-токенов в свободном тексте message/exception.
# `Authorization: Bearer <token>` (case-insensitive), также голый
# `bearer <token>` (без `Authorization:` префикса — встречается в логе httpx
# при кастомных headers={}).
_BEARER_RE = re.compile(
    r"(?i)(authorization\s*[:=]\s*)?bearer\s+[A-Za-z0-9_\-\.]+",
)


def _redact_bearer(text: str) -> str:
    """Замаскировать Bearer-токены в свободной строке.

    Используем регексп, а не парсинг — message от стороннего кода (httpx,
    SQL, traceback) приходит уже текстом, без структурного header'а.
    """
    return _BEARER_RE.sub("Authorization: Bearer <REDACTED>", text)


class _JsonFormatter(logging.Formatter):
    """Formatter, превращающий LogRecord в одну JSON-строку.

    Поля:
      - `timestamp`  — ISO8601 в UTC (Z-суффикс);
      - `level`      — INFO/WARNING/...;
      - `service`    — имя сервиса (из configure_logging);
      - `logger`     — `record.name`;
      - `message`    — отформатированный текст (`record.getMessage()`),
                       прогнан через _redact_bearer;
      - `request_id` — если ContextVar выставлен;
      - `exception`  — `formatException(...)` для ERROR/exc_info, тоже
                       прогнан через _redact_bearer;
      - всё остальное из record.__dict__ — extra-поля.
    """

    def __init__(self, service_name: str) -> None:
        super().__init__()
        self._service = service_name

    def format(self, record: logging.LogRecord) -> str:
        # `record.created` — float epoch. utcfromtimestamp deprecated в 3.12,
        # используем fromtimestamp(tz=utc).
        ts = datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat()
        # ISO8601 даёт `+00:00`; Z-суффикс короче и стандартен для JSON-логов
        # (Elastic / Loki индексируют оба, но Z единообразнее).
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
            # formatException включает traceback с локальными переменными
            # (если formatter настроен) — в нашем случае только стандартный
            # traceback, но Bearer-токены могут прилететь в repr() параметра
            # httpx-вызова. Прогоняем через redact.
            payload["exception"] = _redact_bearer(self.formatException(record.exc_info))

        # Extra-поля: всё что не в LogRecord-стандартных атрибутах.
        # stdlib `logging.makeLogRecord` определяет фиксированный набор;
        # extra=... добавляется как атрибуты, и наш только-наш способ
        # их отличить — list стандартных.
        for key, value in record.__dict__.items():
            if key in _LOG_RECORD_BUILTIN_ATTRS:
                continue
            if key in _RESERVED_FIELDS:
                # Чтобы случайный `extra={"service": ...}` не перетёр
                # обязательное поле, кладём в namespace.
                payload.setdefault("_extra", {})[key] = value
                continue
            payload[key] = value

        return json.dumps(payload, ensure_ascii=False, default=str)


# Стандартные атрибуты LogRecord из stdlib — всё, что не из этого списка,
# трактуется как extra. Скопировано из CPython logging/__init__.py.
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
    """Единая точка настройки логирования.

    Зовётся ОДИН раз при старте сервиса (FastAPI lifespan startup или
    верхний уровень worker'а). Идемпотентна — повторный вызов перенастроит
    handlers корневого logger'а.

    Args:
        service_name: маркер сервиса в каждой записи (`service` поле).
        level: уровень корневого logger'а (`"INFO"`, `"DEBUG"`, ...).
        json_format: если False — fallback на plain-text формат с теми же
            полями (для локальной отладки, когда JSON в терминале нечитаем).
        quiet_loggers: имена logger'ов, которым принудительно ставится
            WARNING — для глушения httpx/httpcore/etc., которые на DEBUG
            утекают Bearer-headers в лог.
    """
    root = logging.getLogger()

    # Сносим существующие handlers — basicConfig из стороннего кода
    # (uvicorn, taskiq) мог уже что-то навесить. Без этого получим двойной
    # вывод: наш JSON + дефолтный plain-text.
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

    # Глушим шумные HTTP-логгеры до WARNING — даже если корень DEBUG.
    # Bearer-токены через httpx-debug — реальная утечка, фиксили вручную в
    # worker'е (`server_worker/src/main.py`), теперь делаем по умолчанию
    # везде.
    for name in quiet_loggers:
        logging.getLogger(name).setLevel(logging.WARNING)
