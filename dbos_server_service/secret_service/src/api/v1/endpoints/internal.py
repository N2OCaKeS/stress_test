"""Internal HTTP-эндпоинты `/internal/lifecycle/*`.

Вызываются auth_service'ом (delete_user / delete_dept) и handler'ом
account_admin'а (revoke department_service_access). Auth — shared bearer
`SERVICE_API_KEY` (или per-caller `SERVICE_API_KEYS`); user-JWT не
принимается, introspect отсюда не дёргается.

Скрыты из публичного OpenAPI: `include_in_schema=False`.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import AppException
from src.dependencies.db import get_db
from src.dependencies.internal_auth import require_internal_caller
from src.schemas.internal import (
    DeptDeletedEvent,
    DeptServiceAccessRevokedEvent,
    LifecycleSummary,
    UserDeletedEvent,
)
from src.services import lifecycle_service

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/internal",
    include_in_schema=False,
    dependencies=[Depends(require_internal_caller)],
)


_INTERNAL_RESPONSES: dict[int | str, dict] = {
    401: {"description": "INTERNAL_AUTH_REQUIRED — bearer отсутствует / не совпал."},
    500: {"description": "LIFECYCLE_HANDLER_FAILED — внутренняя ошибка обработчика."},
}


def _raise_if_errors(summary: dict, op: str) -> None:
    """Транслируем накопленные handler-errors в 500. Транзакция уже откачена."""
    if summary.get("errors"):
        raise AppException(
            error_code="LIFECYCLE_HANDLER_FAILED",
            message=f"{op} failed",
            details={"errors": summary["errors"]},
            http_status=500,
        )


@router.post(
    "/lifecycle/user-deleted",
    response_model=LifecycleSummary,
    status_code=status.HTTP_200_OK,
    responses=_INTERNAL_RESPONSES,
)
async def user_deleted(
    body: UserDeletedEvent,
    db: AsyncSession = Depends(get_db),
) -> LifecycleSummary:
    """Cascade на личные cred'ы удалённого user'а: blocked / hard delete."""
    logger.info("lifecycle: user_deleted user_id=%s actor=%s", body.user_id, body.actor_id)
    summary = await lifecycle_service.handle_user_deleted(db, body.user_id, body.actor_id)
    _raise_if_errors(summary, "handle_user_deleted")
    return LifecycleSummary(
        blocked_count=summary["blocked_count"],
        deleted_count=summary["deleted_count"],
        errors=summary["errors"],
    )


@router.post(
    "/lifecycle/dept-deleted",
    response_model=LifecycleSummary,
    status_code=status.HTTP_200_OK,
    responses=_INTERNAL_RESPONSES,
)
async def dept_deleted(
    body: DeptDeletedEvent,
    db: AsyncSession = Depends(get_db),
) -> LifecycleSummary:
    """Cascade на dep'е: и как owner (blocked), и как recipient (cascade)."""
    logger.info("lifecycle: dept_deleted dept_id=%s actor=%s", body.dept_id, body.actor_id)
    as_owner = await lifecycle_service.handle_dept_deleted_as_owner(
        db, body.dept_id, body.actor_id
    )
    _raise_if_errors(as_owner, "handle_dept_deleted_as_owner")
    as_recipient = await lifecycle_service.handle_dept_deleted_as_recipient(
        db, body.dept_id, body.actor_id
    )
    _raise_if_errors(as_recipient, "handle_dept_deleted_as_recipient")
    return LifecycleSummary(
        blocked_count=as_owner["blocked_count"],
        dept_grants_revoked=as_recipient["dept_grants_revoked"],
        role_acls_revoked=as_recipient["role_acls_revoked"],
        errors=as_owner["errors"] + as_recipient["errors"],
    )


@router.post(
    "/lifecycle/dept-service-access-revoked",
    response_model=LifecycleSummary,
    status_code=status.HTTP_200_OK,
    responses=_INTERNAL_RESPONSES,
)
async def dept_service_access_revoked(
    body: DeptServiceAccessRevokedEvent,
    db: AsyncSession = Depends(get_db),
) -> LifecycleSummary:
    """Cascade на dep'е: снять DeptGrant'ы где он recipient + его RoleACL'и."""
    logger.info(
        "lifecycle: dept_service_access_revoked dept_id=%s service=%s actor=%s",
        body.dept_id, body.service, body.actor_id,
    )
    summary = await lifecycle_service.handle_dept_service_access_revoked(
        db, body.dept_id, body.service, body.actor_id
    )
    _raise_if_errors(summary, "handle_dept_service_access_revoked")
    return LifecycleSummary(
        dept_grants_revoked=summary["dept_grants_revoked"],
        role_acls_revoked=summary["role_acls_revoked"],
        errors=summary["errors"],
    )
