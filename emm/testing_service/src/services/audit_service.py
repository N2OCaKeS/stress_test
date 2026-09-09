"""Публикация audit-событий в loging_service.

`emit()` — best-effort: ошибки httpx не пропагируются в основной запрос.
Любой не переданный параметр подтягивается из `audit_context` (username,
department_id, request_id, ip_address, user_agent, subject_type). `details`
всегда проходит через `redact_payload` — пароли, токены, секреты становятся
типизированными плейсхолдерами.

Async-путь шедулит отправку через `loop.create_task(...)`, sync-путь
(shutdown/startup hook) делает синхронный `httpx.post`. На 429 от
loging_service — до двух дополнительных попыток с exponential backoff ±
jitter; `Retry-After` (delta-seconds) уважается.
"""

from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, timezone

import httpx

from src.core.config import get_settings
from src.core.constants import SERVICE_NAME as _SERVICE_NAME
from src.core.http import bearer_header
from src.services import audit_context
from src.services.audit_events import default_severity
from src.services.redaction import redact_payload

logger = logging.getLogger("audit")

_audit_client: httpx.AsyncClient | None = None
_pending_audit_tasks: "set[asyncio.Task]" = set()

_EVENTS_PATH = "/api/logging/v1/events"

_RETRY_DELAYS_ON_429 = (0.5, 1.5)

_audit_dropped_429: int = 0


def get_dropped_429_total() -> int:
    """Per-process counter дропов на 429. Multi-worker uvicorn → внешний агрегатор суммирует сам."""
    return _audit_dropped_429


def _reset_dropped_429_for_tests() -> None:
    global _audit_dropped_429
    _audit_dropped_429 = 0


async def _post_once(
    client: httpx.AsyncClient | None,
    url: str,
    payload: dict,
    headers: dict,
) -> httpx.Response:
    """Один POST attempt. Pooled при наличии клиента, иначе per-call."""
    if client is not None:
        return await client.post(_EVENTS_PATH, json=payload, headers=headers)
    async with httpx.AsyncClient(timeout=2.0) as fallback:
        return await fallback.post(
            f"{url.rstrip('/')}{_EVENTS_PATH}",
            json=payload,
            headers=headers,
        )


def _parse_retry_after_seconds(value: str | None) -> float | None:
    """Распарсить `Retry-After`. Поддерживается только целочисленный delta-seconds."""
    if not value:
        return None
    v = value.strip()
    if not v:
        return None
    try:
        seconds = float(v)
    except ValueError:
        return None
    if seconds < 0:
        return None
    return min(seconds, 30.0)


def _fallback_stub(payload: dict) -> dict:
    """Sanitized stub для лог-fallback'а: только action+actor+status, без details."""
    return {
        "action": payload.get("action"),
        "actor_id": payload.get("actor_id"),
        "actor_type": payload.get("actor_type"),
        "status": payload.get("status"),
        "request_id": payload.get("request_id"),
    }


async def _send_to_logging_service(payload: dict, url: str, api_key: str) -> None:
    """Async-отправка одного payload'а в loging_service. Все ошибки глушим в WARNING."""
    headers = {**bearer_header(api_key), "X-Service-Identity": "testing_service"}
    rid = payload.get("request_id")
    if rid:
        headers["X-Request-ID"] = str(rid)
    client = _audit_client
    try:
        for attempt in range(len(_RETRY_DELAYS_ON_429) + 1):
            response = await _post_once(client, url, payload, headers)
            if response.status_code != 429:
                return
            if attempt < len(_RETRY_DELAYS_ON_429):
                base_delay = _RETRY_DELAYS_ON_429[attempt] * random.uniform(0.8, 1.2)
                hinted = _parse_retry_after_seconds(response.headers.get("Retry-After"))
                delay = max(base_delay, hinted) if hinted is not None else base_delay
                await asyncio.sleep(delay)
        global _audit_dropped_429
        _audit_dropped_429 += 1
        logger.warning(
            "audit_service: drop after 3x429 (action=%s)", payload.get("action"),
        )
        logger.info("audit_event_fallback %s", _fallback_stub(payload))
    except httpx.HTTPError as exc:
        logger.warning("audit_service: failed to send event: %s", exc)
        logger.info("audit_event_fallback %s", _fallback_stub(payload))
    except Exception as exc:  # noqa: BLE001 — best-effort
        logger.warning("audit_service: unexpected error sending event: %s", exc)
        logger.info("audit_event_fallback %s", _fallback_stub(payload))


def _enrich_details(details: dict | None) -> dict:
    """Добавить ip/user_agent из контекста в details, если их там нет."""
    ctx = audit_context.get_context()
    merged: dict = dict(details) if details else {}
    if ctx.ip_address and "ip" not in merged and "ip_address" not in merged:
        merged["ip"] = ctx.ip_address
    if ctx.user_agent and "user_agent" not in merged and "ua" not in merged:
        merged["user_agent"] = ctx.user_agent
    for k, v in ctx.extra.items():
        merged.setdefault(k, v)
    return merged


def emit(
    action: str,
    actor_id: str | None = None,
    *,
    actor_type: str | None = None,
    target_id: str | None = None,
    target_type: str | None = None,
    service: str = _SERVICE_NAME,
    status: str = "success",
    allowed: bool = True,
    details: dict | None = None,
    request_id: str | None = None,
    department_id: str | None = None,
    department_name: str | None = None,
    username: str | None = None,
    severity: str | None = None,
) -> None:
    """Best-effort emit. Ловит httpx.HTTPError, не пробрасывает наружу."""
    settings = get_settings()
    ctx = audit_context.get_context()

    resolved_actor_id = actor_id if actor_id is not None else ctx.actor_id
    resolved_actor_type = actor_type if actor_type is not None else (ctx.subject_type or "user")
    resolved_username = username if username is not None else ctx.username
    resolved_department = department_id if department_id is not None else ctx.department_id
    resolved_department_name = department_name if department_name is not None else ctx.department_name
    resolved_request_id = request_id if request_id is not None else ctx.request_id
    resolved_severity = severity if severity is not None else default_severity(action, status)

    enriched = _enrich_details(details)
    sanitized = redact_payload(enriched)

    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "service": service,
        "action": action,
        "actor_id": resolved_actor_id,
        "actor_type": resolved_actor_type,
        "username": resolved_username,
        "department_id": resolved_department,
        "department_name": resolved_department_name,
        "target_id": target_id,
        "target_type": target_type,
        "status": status,
        "allowed": allowed,
        "severity": resolved_severity,
        "request_id": resolved_request_id,
        "actor_ip": ctx.ip_address,
        "user_agent": ctx.user_agent,
        "details": sanitized,
    }

    logging_url = getattr(settings, "logging_service_url", None)
    api_key = getattr(settings, "logging_service_api_key", None)

    if not (logging_url and api_key):
        logger.info("audit_event %s", _fallback_stub(payload))
        return

    try:
        loop = asyncio.get_running_loop()
        task = loop.create_task(_send_to_logging_service(payload, logging_url, api_key))
        _pending_audit_tasks.add(task)
        task.add_done_callback(_pending_audit_tasks.discard)
    except RuntimeError:
        _send_sync(payload, logging_url, api_key)


_SYNC_RETRY_DELAYS_ON_429 = (0.2, 0.5)


def _send_sync(payload: dict, logging_url: str, api_key: str) -> None:
    """Sync-отправка с симметричным async-пути retry на 429. Кап ~0.7s суммарно."""
    url_full = f"{logging_url}/api/logging/v1/events"
    headers = {**bearer_header(api_key), "X-Service-Identity": "testing_service"}
    rid = payload.get("request_id")
    if rid:
        headers["X-Request-ID"] = str(rid)
    dropped_429 = False
    try:
        for attempt in range(len(_SYNC_RETRY_DELAYS_ON_429) + 1):
            try:
                response = httpx.post(url_full, json=payload, headers=headers, timeout=2.0)
            except httpx.HTTPError as exc:
                logger.warning("audit_service: failed to send event (sync): %s", exc)
                logger.info("audit_event_fallback %s", _fallback_stub(payload))
                return
            if response.status_code != 429:
                return
            if attempt < len(_SYNC_RETRY_DELAYS_ON_429):
                base_delay = _SYNC_RETRY_DELAYS_ON_429[attempt]
                hinted = _parse_retry_after_seconds(response.headers.get("Retry-After"))
                delay = min(max(base_delay, hinted) if hinted is not None else base_delay, 1.0)
                import time as _time
                try:
                    _time.sleep(delay)
                except BaseException:
                    dropped_429 = True
                    raise
        dropped_429 = True
        logger.warning(
            "audit_service: drop after 3x429 sync (action=%s)", payload.get("action"),
        )
        logger.info("audit_event_fallback %s", _fallback_stub(payload))
    except Exception as exc:  # noqa: BLE001
        logger.warning("audit_service: unexpected error (sync): %s", exc)
    finally:
        if dropped_429:
            global _audit_dropped_429
            _audit_dropped_429 += 1
