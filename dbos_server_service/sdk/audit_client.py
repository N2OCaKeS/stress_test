"""Reusable audit client for DBOS platform services.

Quick start — copy this file into your service and configure via env vars:

    LOGGING_SERVICE_URL=http://localhost:8001
    LOGGING_SERVICE_API_KEY=<shared-secret>
    SERVICE_NAME=your_service          # defaults to hostname if unset

Then call:

    from audit_client import emit

    emit("user.login", actor_id="usr_123", status="success")
    emit("resource.delete", actor_id="usr_456", target_id="res_789", status="failure")

The client never raises — failed deliveries are logged as WARNING and the event
is echoed to the Python logger so nothing is silently lost.

For async services (FastAPI / asyncio), wrap in asyncio.to_thread():

    import asyncio
    asyncio.ensure_future(asyncio.to_thread(emit, "user.login", actor_id=...))
"""

import logging
import os
import socket
from datetime import datetime, timezone
from typing import Any

import httpx

logger = logging.getLogger("audit")

_SERVICE_NAME = os.getenv("SERVICE_NAME") or socket.gethostname()
_LOGGING_URL = os.getenv("LOGGING_SERVICE_URL")
_API_KEY = os.getenv("LOGGING_SERVICE_API_KEY")
_TIMEOUT = float(os.getenv("LOGGING_SERVICE_TIMEOUT", "2.0"))


class AuditClient:
    """Configurable audit client — use when you need multiple named instances."""

    def __init__(
        self,
        service_name: str,
        logging_url: str | None = None,
        api_key: str | None = None,
        timeout: float = 2.0,
    ) -> None:
        self.service_name = service_name
        self.logging_url = logging_url or _LOGGING_URL
        self.api_key = api_key or _API_KEY
        self.timeout = timeout

    def emit(
        self,
        action: str,
        actor_id: str | None = None,
        *,
        actor_type: str = "service",
        target_id: str | None = None,
        target_type: str | None = None,
        status: str = "success",
        allowed: bool = True,
        details: dict[str, Any] | None = None,
        request_id: str | None = None,
        department_id: str | None = None,
    ) -> None:
        _send(
            service=self.service_name,
            action=action,
            actor_id=actor_id,
            actor_type=actor_type,
            target_id=target_id,
            target_type=target_type,
            status=status,
            allowed=allowed,
            details=details,
            request_id=request_id,
            department_id=department_id,
            logging_url=self.logging_url,
            api_key=self.api_key,
            timeout=self.timeout,
        )


def emit(
    action: str,
    actor_id: str | None = None,
    *,
    actor_type: str = "service",
    target_id: str | None = None,
    target_type: str | None = None,
    service: str | None = None,
    status: str = "success",
    allowed: bool = True,
    details: dict[str, Any] | None = None,
    request_id: str | None = None,
    department_id: str | None = None,
) -> None:
    """Module-level shortcut — reads config from env vars."""
    _send(
        service=service or _SERVICE_NAME,
        action=action,
        actor_id=actor_id,
        actor_type=actor_type,
        target_id=target_id,
        target_type=target_type,
        status=status,
        allowed=allowed,
        details=details,
        request_id=request_id,
        department_id=department_id,
        logging_url=_LOGGING_URL,
        api_key=_API_KEY,
        timeout=_TIMEOUT,
    )


def _send(
    *,
    service: str,
    action: str,
    actor_id: str | None,
    actor_type: str,
    target_id: str | None,
    target_type: str | None,
    status: str,
    allowed: bool,
    details: dict[str, Any] | None,
    request_id: str | None,
    department_id: str | None,
    logging_url: str | None,
    api_key: str | None,
    timeout: float,
) -> None:
    payload: dict[str, Any] = {
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

    if logging_url and api_key:
        try:
            httpx.post(
                f"{logging_url}/api/logging/v1/events",
                json=payload,
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=timeout,
            )
        except Exception as exc:
            logger.warning("audit: delivery failed (%s) — event: %s", exc, payload)
    else:
        logger.info("audit_event (no logging_service configured): %s", payload)


# ── Event registration ────────────────────────────────────────────────────────

def register_events(
    events: list[dict[str, Any]],
    service: str | None = None,
    logging_url: str | None = None,
    api_key: str | None = None,
    timeout: float = 5.0,
) -> None:
    """Register the service's event list in loging_service.

    Call once on startup — idempotent (upserts).

    events: list of dicts with keys:
      action         (required) e.g. "user.login"
      description    (optional) human-readable description
      default_severity (optional) e.g. "INFO"

    Example:
        register_events([
            {"action": "user.login", "description": "User auth attempt", "default_severity": "INFO"},
            {"action": "user.ban",   "description": "User banned",        "default_severity": "CRITICAL"},
        ])
    """
    svc = service or _SERVICE_NAME
    url = logging_url or _LOGGING_URL
    key = api_key or _API_KEY
    if not url or not key:
        logger.debug("audit: skipping event registration — LOGGING_SERVICE_URL not configured")
        return
    try:
        resp = httpx.post(
            f"{url}/api/logging/v1/services/{svc}/events",
            json={"events": events},
            headers={"Authorization": f"Bearer {key}"},
            timeout=timeout,
        )
        if resp.status_code == 200:
            d = resp.json()
            logger.info("audit: registered %d events (added=%d updated=%d)", d["total"], d["added"], d["updated"])
        else:
            logger.warning("audit: event registration failed: %s", resp.status_code)
    except Exception as exc:
        logger.warning("audit: event registration error: %s", exc)
