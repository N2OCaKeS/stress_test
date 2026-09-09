"""Каталог audit-событий, которые эмитит testing_service.

Регистрируется в loging_service на startup через `register_events()`. Пока
пусто по бизнес-событиям — `test.launch`/`test.cancel`/`stp.status_updated`/
`global_variable.created`/... из §11 плана миграции появятся вместе с
доменом, который их производит (волны 3+). Инфраструктурные события
(lifecycle + HTTP middleware) заведены сразу — их эмитит уже этот каркас.
"""

from __future__ import annotations

import logging

import httpx

from src.core.config import get_settings
from src.core.constants import SERVICE_NAME as _SERVICE_NAME
from src.core.http import bearer_header

logger = logging.getLogger("audit")

SERVICE_EVENTS = [
    {"action": "service.started", "description": "Service started up", "default_severity": "INFO"},
    {"action": "http.access_denied", "description": "HTTP 401/403 response", "default_severity": "CRITICAL"},
    {"action": "http.client_error", "description": "HTTP 4xx response (except 401/403)", "default_severity": "WARNING"},
    {"action": "http.server_error", "description": "HTTP 5xx response", "default_severity": "CRITICAL"},
]

_DEFAULT_SEVERITY: dict[tuple[str, str], str] = {
    ("service.started", "success"): "INFO",
    ("http.access_denied", "failure"): "CRITICAL",
    ("http.client_error", "failure"): "WARNING",
    ("http.server_error", "failure"): "CRITICAL",
}


def default_severity(action: str, status: str) -> str | None:
    """Подсказка severity для пары (action, status). None — нет дефолта."""
    return _DEFAULT_SEVERITY.get((action, status))


def register_events() -> None:
    """POST полного списка событий в loging_service. Вызывается на startup.

    Не блокирует startup — при недоступности loging_service логируем WARNING.
    """
    settings = get_settings()
    logging_url = getattr(settings, "logging_service_url", None)
    api_key = getattr(settings, "logging_service_api_key", None)
    if not logging_url or not api_key:
        logger.debug("audit: skipping event registration — LOGGING_SERVICE_URL not configured")
        return
    try:
        resp = httpx.post(
            f"{logging_url}/api/logging/v1/services/{_SERVICE_NAME}/events",
            json={"events": SERVICE_EVENTS},
            headers={**bearer_header(api_key), "X-Service-Identity": "testing_service"},
            timeout=2.0,
        )
        if resp.status_code == 200:
            data = resp.json()
            logger.info(
                "audit: registered %d events (added=%d updated=%d)",
                data.get("total"), data.get("added"), data.get("updated"),
            )
        else:
            logger.warning("audit: event registration failed: %s %s", resp.status_code, resp.text)
    except Exception as exc:  # noqa: BLE001 — best-effort
        logger.warning("audit: event registration error: %s", exc)
