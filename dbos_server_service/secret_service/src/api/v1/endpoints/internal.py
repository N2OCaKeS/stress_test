"""Internal HTTP-эндпоинты `/internal/lifecycle/*`.

Вызываются auth_service'ом (delete_user / delete_dept) и handler'ом
account_admin'а (revoke department_service_access). Auth — shared bearer
`SERVICE_API_KEY` (или per-caller `SERVICE_API_KEYS`); user-JWT не
принимается, introspect отсюда не дёргается.

Скрыты из публичного OpenAPI: `include_in_schema=False`.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Body, Depends, Path, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import AppException
from src.core.keystore import get_keystore
from src.dependencies.db import get_db
from src.dependencies.internal_auth import (
    require_caller_identity,
    require_internal_caller,
)
from src.schemas.internal import (
    DeptDeletedEvent,
    DeptServiceAccessRevokedEvent,
    LifecycleSummary,
    MigrationStatus,
    RetireKeyResponse,
    RotateKeyResponse,
    UserDeletedEvent,
)
from src.services import (
    audit_service,
    key_rotation_service,
    lifecycle_service,
    migration_status_service,
)

logger = logging.getLogger(__name__)

# Lifecycle-эндпоинты обслуживают строго auth_service — отдельный guard на
# X-Service-Identity. Любой другой caller с действительным SERVICE_API_KEY
# отбивается 401 WRONG_CALLER.
_require_auth_service = require_caller_identity("auth_service")

router = APIRouter(
    prefix="/internal",
    include_in_schema=False,
    dependencies=[Depends(_require_auth_service)],
)

# Отдельный sub-router без жёсткой привязки caller'а: ротационный скрипт
# может прийти из k8s job'а под service identity ротатора, а не auth_service'а.
# Достаточно валидного bearer'а из SERVICE_API_KEY / SERVICE_API_KEYS.
ops_router = APIRouter(
    prefix="/internal",
    include_in_schema=False,
    dependencies=[Depends(require_internal_caller)],
)


_INTERNAL_RESPONSES: dict[int | str, dict] = {
    401: {"description": "INTERNAL_AUTH_REQUIRED — bearer отсутствует / не совпал / wrong caller."},
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


@ops_router.get(
    "/migration_status",
    response_model=MigrationStatus,
    status_code=status.HTTP_200_OK,
    responses={
        401: {"description": "INTERNAL_AUTH_REQUIRED — bearer отсутствует / не совпал."},
    },
)
async def migration_status(
    db: AsyncSession = Depends(get_db),
) -> MigrationStatus:
    """Прогресс lazy re-encrypt'а под активную версию мастер-ключа.

    Группирует строки `credentials` по wire-префиксу `v<N>$` и считает,
    сколько осталось переехать. Гейт для `--finalize` в ротационном скрипте:
    дропать `SECRET_ENCRYPTION_KEY__v<N>` из env'а можно только когда
    `remaining_legacy == 0`.
    """
    active_version = get_keystore().get_active_version()
    return await migration_status_service.compute(db, active_version=active_version)


@ops_router.post(
    "/encryption/rotate",
    response_model=RotateKeyResponse,
    responses={
        200: {"description": "Новая версия активна, reencrypt-outbox засеян."},
        401: {"description": "INTERNAL_AUTH_REQUIRED — bearer отсутствует / не совпал."},
        400: {"description": "ROTATE_KEY_INVALID — new_key_b64 не base64/не 32 байта."},
    },
)
async def rotate_encryption_key(
    request: Request,
    db: AsyncSession = Depends(get_db),
    new_key_b64: str = Body(
        embed=True,
        min_length=1,
        description=(
            "Новый master-материал в base64 (32 байта). Сгенерить через"
            " auth_service `POST /admin/service-keys/generate`."
        ),
    ),
) -> RotateKeyResponse:
    """Рантайм-ротация мастер-ключа secret_service без простоя.

    Новая версия становится активной (новые токены сразу под ней), старые
    токены остаются читаемыми (материал старых версий ещё в keystore),
    фоновая ре-шифрация публикуется через reencrypt-outbox. Идемпотентно:
    повтор с тем же материалом не плодит версию.

    Audit: `secrets.encryption_rotate` (CRITICAL).
    """
    data = await key_rotation_service.rotate(db, new_key_b64=new_key_b64)
    caller = getattr(request.state, "caller", None)
    audit_service.emit(
        "secrets.encryption_rotate",
        target_id=None,
        target_type="secret",
        status="success",
        allowed=True,
        details={
            "caller": caller,
            "new_version": data["new_version"],
            "previous_version": data["previous_version"],
            "seeded_inserted": data["seeded"]["inserted"],
            "idempotent": data["idempotent"],
        },
    )
    return RotateKeyResponse(**data)


@ops_router.post(
    "/encryption/retire/{version}",
    response_model=RetireKeyResponse,
    responses={
        200: {"description": "Версия убрана из keystore (или уже отсутствовала)."},
        401: {"description": "INTERNAL_AUTH_REQUIRED."},
        409: {"description": "KEYSTORE_CANNOT_RETIRE_ACTIVE / KEYSTORE_VERSION_IN_USE."},
    },
)
async def retire_encryption_key(
    request: Request,
    version: int = Path(..., ge=1),
    db: AsyncSession = Depends(get_db),
) -> RetireKeyResponse:
    """Убрать старую версию мастер-ключа из keystore.

    Разрешено только когда на версии 0 строк (полная ре-шифрация завершена)
    и она не активна — иначе 409.

    Audit: `secrets.encryption_retire` (CRITICAL).
    """
    data = await key_rotation_service.retire(db, version=version)
    caller = getattr(request.state, "caller", None)
    audit_service.emit(
        "secrets.encryption_retire",
        target_id=None,
        target_type="secret",
        status="success",
        allowed=True,
        details={
            "caller": caller,
            "version": data["version"],
            "retired": data["retired"],
        },
    )
    return RetireKeyResponse(**data)
