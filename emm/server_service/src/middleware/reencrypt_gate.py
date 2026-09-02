"""Maintenance-gate: при активном force-режиме перешифровки закрывает сервис.

Когда оператор запускает форсированную ротацию мастер-ключа (`rotate` с
`mode=force`), взводится durable флаг `secrets_migration_state.force_active`.
Пока он взведён, любой запрос — кроме статуса перешифровки и health/ready-проб
— получает `503 REENCRYPT_IN_PROGRESS` + `Retry-After`. Дренер осушает outbox
и по завершении сам снимает флаг, разблокируя сервис.

### Что НЕ блокируется

* **Статус перешифровки** (`*/migration_status`) — UI и rotation-runner поллят
  прогресс именно через него; блокировать его бессмысленно.
* **`/health` и `/ready`** — k8s-пробы. Если отбивать их 503'ами, kubelet
  прибьёт под, а вместе с ним и дренер, который как раз и осушает миграцию.
  Это техническая необходимость, а не бизнес-исключение.

### Порядок middleware

Регистрируется внутрь `attach_request_id_and_context` (нужен request_id в
envelope) и наружу `platform_admin_guard`/`audit_access` — 503 короткозамкнут
до аудита, чтобы maintenance-трафик не флудил audit-канал `http.server_error`'ами.
Security-заголовки навешиваются `SecurityHeadersMiddleware` снаружи, так что
503-ответ их всё равно получает.

Состояние force-флага читается через `force_gate_state_cached` (короткий
TTL-кэш поверх БД) — read не долбит БД на каждый запрос, а при снятии force
сервис разблокируется с задержкой не больше TTL.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import Request
from starlette.responses import JSONResponse

from src.core.constants import HEALTH_PATHS
from src.services import secrets_migration_service

logger = logging.getLogger(__name__)


def _is_status_path(path: str) -> bool:
    """True для эндпоинтов статуса перешифровки (`*/migration_status`).

    Покрывает все три канала: admin (`/admin/encryption/migration_status`),
    ops (`/internal/migration_status`) и worker (`/internal/secrets/migration_status`).
    """
    return path.endswith("/migration_status")


def _is_exempt(path: str) -> bool:
    """Пути, которые gate пропускает даже при активном force-режиме."""
    return path in HEALTH_PATHS or _is_status_path(path)


async def reencrypt_maintenance_gate(request: Request, call_next):
    """ASGI middleware: при force-режиме отбивает не-exempt запросы 503'ами."""
    path = request.url.path
    if _is_exempt(path):
        return await call_next(request)

    state = await secrets_migration_service.force_gate_state_cached()
    if not state["force_active"]:
        return await call_next(request)

    retry_after = int(state["retry_after"])
    return JSONResponse(
        status_code=503,
        headers={"Retry-After": str(retry_after)},
        content={
            "error": "service_unavailable",
            "error_code": "REENCRYPT_IN_PROGRESS",
            "message": (
                "Server is re-encrypting stored secrets under a new master key "
                "(force maintenance mode); retry after the indicated delay."
            ),
            "details": {
                "retry_after": retry_after,
                "eta_seconds": state["eta_seconds"],
                "remaining": state["remaining"],
            },
            "request_id": getattr(request.state, "request_id", None),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    )
