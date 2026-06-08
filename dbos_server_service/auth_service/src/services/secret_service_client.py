"""Outbound client для lifecycle-уведомлений в secret_service.

auth_service эмитит lifecycle-события (user удалён, отдел удалён, у отдела
отозван access к secret_service) → secret_service делает каскадный
block/revoke на personal-кред, DeptGrant, RoleACL.

Контракт:

* POST `/api/secret/v1/internal/lifecycle/{user-deleted,dept-deleted,
  dept-service-access-revoked}`.
* Bearer = `settings.secret_internal_api_key` (shared service-bearer, тот же
  что в `SERVICE_API_KEYS` на стороне secret_service).
* TLS verify контролируется `settings.secret_service_tls_verify` (для
  dev/test, где сертификат self-signed, выставляем `False` через env).
* Best-effort: любая ошибка → `logger.warning` + audit-emit
  `secret_lifecycle.notify_failed`, наружу не пробрасываем. secret_service
  собирает «осиротевшие» события через Phase 7 sweep'ом, поэтому потеря
  одного callback'а не ломает каскад.
* `secret_service_url` пустой → клиент молча skip'ает emit с WARNING
  (dev/test без secret_service'а, без сообщения операторам).

Пул httpx.AsyncClient инициализируется лениво из main.lifespan через
`init_pool(settings)`, дренируется через `aclose_pool()`. Если пул не
поднят (например, в unit-тесте без lifespan'а) — fallback на per-call
`httpx.AsyncClient`.
"""

from __future__ import annotations

import logging

import httpx

from src.core.config import get_settings
from src.core.http import bearer_header
from src.services import audit_service

logger = logging.getLogger(__name__)


# Слот под pooled-клиент. None пока lifespan не вызвал `init_pool`.
# Тесты, импортирующие модуль до startup'а, идут по fallback-пути.
_client: httpx.AsyncClient | None = None

_LIFECYCLE_PATH = "/api/secret/v1/internal/lifecycle"
_TIMEOUT_SECONDS = 5.0
_MAX_CONNECTIONS = 10
_MAX_KEEPALIVE = 5


def init_pool(settings) -> None:
    """Поднять pooled httpx-client под lifecycle-callback'и. Идемпотентно.

    Если `secret_service_url` пуст — клиент не создаётся; notify_*
    обнаружит пустой URL и уйдёт в no-op-with-warning.
    """
    global _client
    url = (getattr(settings, "secret_service_url", "") or "").strip()
    if not url:
        return
    if _client is not None:
        return
    verify = bool(getattr(settings, "secret_service_tls_verify", True))
    _client = httpx.AsyncClient(
        base_url=url.rstrip("/"),
        timeout=httpx.Timeout(_TIMEOUT_SECONDS, connect=2.0, write=2.0, pool=2.0),
        limits=httpx.Limits(
            max_connections=_MAX_CONNECTIONS,
            max_keepalive_connections=_MAX_KEEPALIVE,
        ),
        verify=verify,
    )


async def aclose_pool() -> None:
    """Закрыть pooled-client и обнулить slot. Idempotent."""
    global _client
    client = _client
    _client = None
    if client is not None:
        try:
            await client.aclose()
        except Exception as exc:  # shutdown best-effort
            logger.warning("secret_service_client: failed to close pool: %s", exc)


def reset_for_tests() -> None:
    """Синхронный сброс slot'а без `aclose` — для monkeypatch'а в тестах."""
    global _client
    _client = None


async def _post(
    suffix: str,
    payload: dict,
    actor_id: str | None,
    actor_username: str | None,
) -> None:
    """POST одного lifecycle-события. Best-effort: ошибки в audit + warning."""
    settings = get_settings()
    url = (getattr(settings, "secret_service_url", "") or "").strip()
    if not url:
        logger.warning(
            "secret_service_client: SECRET_SERVICE_URL is empty, skipping %s",
            suffix,
        )
        return

    api_key = getattr(settings, "secret_internal_api_key", "") or ""
    headers = bearer_header(api_key)
    headers["Content-Type"] = "application/json"

    full_path = f"{_LIFECYCLE_PATH}/{suffix}"

    try:
        if _client is not None:
            response = await _client.post(full_path, json=payload, headers=headers)
        else:
            verify = bool(getattr(settings, "secret_service_tls_verify", True))
            async with httpx.AsyncClient(
                timeout=_TIMEOUT_SECONDS, verify=verify,
            ) as fallback:
                response = await fallback.post(
                    f"{url.rstrip('/')}{full_path}",
                    json=payload,
                    headers=headers,
                )
    except Exception as exc:
        logger.warning(
            "secret_service_client: %s failed (transport): %s", suffix, exc,
        )
        audit_service.emit(
            "secret_lifecycle.notify_failed",
            actor_id=actor_id,
            username=actor_username,
            status="failure",
            allowed=True,
            details={
                "endpoint": suffix,
                "reason": "transport_error",
                "error": str(exc),
                "payload": payload,
            },
        )
        return

    if response.status_code >= 400:
        logger.warning(
            "secret_service_client: %s returned %d (best-effort, не падаем)",
            suffix,
            response.status_code,
        )
        # Тело может быть бинарным/пустым — берём только safe-срез текста.
        body_preview = ""
        try:
            body_preview = response.text[:500]
        except Exception:
            body_preview = "<unreadable>"
        audit_service.emit(
            "secret_lifecycle.notify_failed",
            actor_id=actor_id,
            username=actor_username,
            status="failure",
            allowed=True,
            details={
                "endpoint": suffix,
                "reason": "http_error",
                "status_code": response.status_code,
                "body_preview": body_preview,
                "payload": payload,
            },
        )


async def notify_user_deleted(
    user_id: str,
    actor_id: str | None,
    actor_username: str | None,
) -> None:
    """Уведомить secret_service об удалении юзера. Best-effort."""
    await _post(
        "user-deleted",
        {
            "user_id": user_id,
            "actor_id": actor_id,
            "actor_username": actor_username,
        },
        actor_id,
        actor_username,
    )


async def notify_dept_deleted(
    dept_id: str,
    actor_id: str | None,
    actor_username: str | None,
) -> None:
    """Уведомить secret_service об удалении отдела. Best-effort."""
    await _post(
        "dept-deleted",
        {
            "dept_id": dept_id,
            "actor_id": actor_id,
            "actor_username": actor_username,
        },
        actor_id,
        actor_username,
    )


async def notify_dept_service_access_revoked(
    dept_id: str,
    service: str,
    actor_id: str | None,
    actor_username: str | None,
) -> None:
    """Уведомить secret_service, что отдел потерял access к сервису.

    Сами шлём только когда `service == "secret_service"`: если у dep'а
    отозвали access к docker_registry или server_service — secret_service
    это не касается, шум только.
    """
    await _post(
        "dept-service-access-revoked",
        {
            "dept_id": dept_id,
            "service": service,
            "actor_id": actor_id,
            "actor_username": actor_username,
        },
        actor_id,
        actor_username,
    )
