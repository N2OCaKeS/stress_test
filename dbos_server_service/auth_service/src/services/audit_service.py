"""Audit event publishing to logging_service.

Плотная интеграция:
- Любой вызов `emit(action, actor_id=None, ...)` автоматически подхватывает из
  `audit_context`: username, department_id, request_id, ip_address, user_agent.
  Если параметр передан явно — он имеет приоритет.
- `details` всегда проходит через `redaction.redact()` — пароли, токены, секреты
  и хэши превращаются в типизированные плейсхолдеры (`<PASSWORD>`, `<TOKEN>`, …).
- При наличии в контексте ip_address/user_agent они добавляются в details
  автоматически (если уже не присутствуют).
"""

import asyncio
import logging
from datetime import datetime, timezone

import httpx

from src.core.config import get_settings
from src.services import audit_context
from src.services.redaction import redact

logger = logging.getLogger("audit")


async def _send_to_logging_service(payload: dict, url: str, api_key: str) -> None:
    async with httpx.AsyncClient(timeout=2.0) as client:
        try:
            await client.post(
                f"{url}/api/logging/v1/events",
                json=payload,
                headers={"Authorization": f"Bearer {api_key}"},
            )
        except Exception as exc:
            logger.warning("audit_service: failed to send event: %s", exc)
            logger.info("audit_event_fallback %s", payload)


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
    actor_type: str = "user",
    target_id: str | None = None,
    target_type: str | None = None,
    service: str = "auth_service",
    status: str = "success",
    allowed: bool = True,
    details: dict | None = None,
    request_id: str | None = None,
    department_id: str | None = None,
    username: str | None = None,
) -> None:
    settings = get_settings()
    ctx = audit_context.get_context()

    # Приоритет: явный параметр > контекст
    resolved_actor_id = actor_id if actor_id is not None else ctx.actor_id
    resolved_username = username if username is not None else ctx.username
    resolved_department = department_id if department_id is not None else ctx.department_id
    resolved_request_id = request_id if request_id is not None else ctx.request_id

    # Маскировка details + автозаполнение ip/ua
    enriched = _enrich_details(details)
    sanitized = redact(enriched)

    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "service": service,
        "action": action,
        "actor_id": resolved_actor_id,
        "actor_type": actor_type,
        "username": resolved_username,
        "department_id": resolved_department,
        "target_id": target_id,
        "target_type": target_type,
        "status": status,
        "allowed": allowed,
        "request_id": resolved_request_id,
        "details": sanitized,
    }

    logging_url = getattr(settings, "logging_service_url", None)
    api_key = getattr(settings, "logging_service_api_key", None)

    if logging_url and api_key:
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(_send_to_logging_service(payload, logging_url, api_key))
        except RuntimeError:
            # Called from a sync context (e.g. asyncio.to_thread in middleware)
            try:
                httpx.post(
                    f"{logging_url}/api/logging/v1/events",
                    json=payload,
                    headers={"Authorization": f"Bearer {api_key}"},
                    timeout=2.0,
                )
            except Exception as exc:
                logger.warning("audit_service: failed to send event: %s", exc)
                logger.info("audit_event_fallback %s", payload)
    else:
        logger.info("audit_event %s", payload)
