"""Audit event publishing to logging_service."""

import logging
from datetime import datetime, timezone

import httpx

from src.core.config import get_settings

logger = logging.getLogger("audit")


def emit(
    action: str,
    actor_id: str | None,
    actor_type: str = "user",
    target_id: str | None = None,
    target_type: str | None = None,
    service: str = "auth_service",
    status: str = "success",
    allowed: bool = True,
    details: dict | None = None,
    request_id: str | None = None,
    department_id: str | None = None,
) -> None:
    settings = get_settings()
    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "service": service,
        "action": action,
        "actor_id": actor_id,
        "actor_type": actor_type,
        "department_id": department_id,
        "target_id": target_id,
        "target_type": target_type,
        "status": status,
        "allowed": allowed,
        "request_id": request_id,
        "details": details or {},
    }

    logging_url = getattr(settings, "logging_service_url", None)
    api_key = getattr(settings, "logging_service_api_key", None)

    if logging_url and api_key:
        try:
            httpx.post(
                f"{logging_url}/api/logging/v1/events",
                json=payload,
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=2.0,
            )
        except Exception as exc:
            logger.warning("audit_service: failed to send event to logging_service: %s", exc)
            logger.info("audit_event_fallback %s", payload)
    else:
        logger.info("audit_event %s", payload)
