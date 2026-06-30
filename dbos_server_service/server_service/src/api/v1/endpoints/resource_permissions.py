"""Эндпоинты инстанс-уровневого ACL (resource_role_permissions).

Точечные гранты роли на КОНКРЕТНЫЙ ресурс (server / server_account) поверх
тип-wide матрицы. Авторизация — как у `/permissions`: `(permission, *,
permission_grant/revoke/view)` либо платформенный `account_admin` (мета-админ).
Только инстанс-привязанные действия (`create` и callback'и воркера остаются в
глобальном слое `/permissions`).
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.dependencies.auth import PermissionMatrixIdentity
from src.dependencies.db import get_db
from src.schemas.common import OkResponse
from src.schemas.resource_permission import (
    ResourcePermissionListResponse,
    ResourcePermissionPropagateRequest,
    ResourcePermissionResponse,
    ResourcePropagateResponse,
)
from src.services import resource_permission_service

router = APIRouter(prefix="/resource-permissions")


@router.get(
    "/by-resource/{resource_type}/{resource_id}",
    response_model=ResourcePermissionListResponse,
    summary="Инстанс-гранты на один ресурс",
    description=(
        "Список точечных грантов на конкретный ресурс (server / server_account). "
        "Доступ: `(permission, *, view)` своего отдела либо account_admin. "
        "Неизвестный resource_type → 422 UNKNOWN_RESOURCE_TYPE."
    ),
    responses={
        403: {"description": "Нет роли с `view` на permission."},
        422: {"description": "UNKNOWN_RESOURCE_TYPE."},
    },
)
async def list_for_resource(
    resource_type: str,
    resource_id: str,
    identity: PermissionMatrixIdentity,
    db: AsyncSession = Depends(get_db),
) -> ResourcePermissionListResponse:
    rows = await resource_permission_service.list_for_resource(
        db, identity, resource_type, resource_id
    )
    items = [ResourcePermissionResponse.model_validate(r) for r in rows]
    return ResourcePermissionListResponse(items=items, total=len(items))


@router.get(
    "/by-role/{role}",
    response_model=ResourcePermissionListResponse,
    summary="Инстанс-гранты одной роли",
    description=(
        "Срез всех точечных грантов одной роли (опц. `resource_type=` сужает). "
        "Доступ: `(permission, *, view)` своего отдела либо account_admin."
    ),
    responses={403: {"description": "Нет роли с `view` на permission."}},
)
async def list_for_role(
    role: str,
    identity: PermissionMatrixIdentity,
    resource_type: str | None = Query(
        default=None, max_length=64,
        description="Сузить выдачу до одного resource_type (server / server_account).",
    ),
    db: AsyncSession = Depends(get_db),
) -> ResourcePermissionListResponse:
    rows = await resource_permission_service.list_for_role(
        db, identity, role, resource_type=resource_type
    )
    items = [ResourcePermissionResponse.model_validate(r) for r in rows]
    return ResourcePermissionListResponse(items=items, total=len(items))


@router.put(
    "/{resource_type}/{resource_id}/{role}/{action}",
    response_model=ResourcePermissionResponse,
    summary="Выдать инстанс-грант `action` роли на ресурс",
    description=(
        "Идемпотентно (повтор того же effect → возврат существующей строки; "
        "иной effect → обновление allow↔deny). `effect=allow` (дефолт) добавляет "
        "право поверх тип-wide матрицы, `effect=deny` запрещает его этой роли на "
        "ресурсе (override базы). Действие обязано быть инстанс-грантуемым "
        "(`create` и callback'и воркера → 422 ACTION_NOT_INSTANCE_GRANTABLE). "
        "Системные роли `admin`/`guest` неизменяемы → 409 SYSTEM_ROLE_IMMUTABLE. "
        "Ресурс обязан быть в отделе актора (иначе 404). Scope строки — отдел ресурса."
    ),
    responses={
        403: {"description": "PERMISSION_DENIED."},
        404: {"description": "RESOURCE_NOT_FOUND — ресурс не найден / чужой отдел."},
        409: {"description": "RESOURCE_PERMISSION_ALREADY_EXISTS / SYSTEM_ROLE_IMMUTABLE."},
        422: {"description": "UNKNOWN_RESOURCE_TYPE / ACTION_NOT_INSTANCE_GRANTABLE."},
    },
)
async def grant_permission(
    resource_type: str,
    resource_id: str,
    role: str,
    action: str,
    identity: PermissionMatrixIdentity,
    effect: str = Query(
        default="allow",
        pattern="^(allow|deny)$",
        description="allow (дефолт) — добавить право; deny — запретить роли на ресурсе.",
    ),
    db: AsyncSession = Depends(get_db),
) -> ResourcePermissionResponse:
    obj = await resource_permission_service.grant_action(
        db, identity,
        resource_type=resource_type, resource_id=resource_id,
        role=role, action=action, effect=effect,
    )
    return ResourcePermissionResponse.model_validate(obj)


@router.delete(
    "/{resource_type}/{resource_id}/{role}/{action}",
    response_model=OkResponse,
    summary="Снять инстанс-грант `action` с роли на ресурсе",
    description=(
        "Удаляет точечный грант (allow или deny). Отсутствие строки → 404. "
        "Системные роли `admin`/`guest` неизменяемы → 409 SYSTEM_ROLE_IMMUTABLE."
    ),
    responses={
        403: {"description": "PERMISSION_DENIED."},
        404: {"description": "RESOURCE_NOT_FOUND / RESOURCE_PERMISSION_NOT_FOUND."},
        409: {"description": "SYSTEM_ROLE_IMMUTABLE."},
        422: {"description": "UNKNOWN_RESOURCE_TYPE."},
    },
)
async def revoke_permission(
    resource_type: str,
    resource_id: str,
    role: str,
    action: str,
    identity: PermissionMatrixIdentity,
    db: AsyncSession = Depends(get_db),
) -> OkResponse:
    await resource_permission_service.revoke_action(
        db, identity,
        resource_type=resource_type, resource_id=resource_id,
        role=role, action=action,
    )
    return OkResponse()


@router.post(
    "/{resource_type}/{source_resource_id}/propagate",
    response_model=ResourcePropagateResponse,
    summary="Распространить инстанс-гранты образца на список целей",
    description=(
        "Копирует ВСЕ инстанс-гранты ресурса-образца на однотипные цели. "
        "`mode=merge` (дефолт) добавляет недостающее; `mode=mirror` приводит "
        "цели к точной копии образца (добавляет недостающее + удаляет лишнее, "
        "требует и permission_grant, и permission_revoke). Идемпотентно. "
        "Все цели обязаны быть в отделе образца (иначе 404). Сводка — "
        "added/removed на каждую цель."
    ),
    responses={
        403: {"description": "PERMISSION_DENIED (grant; mirror требует и revoke)."},
        404: {"description": "RESOURCE_NOT_FOUND — образец/цель не найдены / чужой отдел."},
        422: {"description": "UNKNOWN_RESOURCE_TYPE."},
    },
)
async def propagate_permissions(
    resource_type: str,
    source_resource_id: str,
    body: ResourcePermissionPropagateRequest,
    identity: PermissionMatrixIdentity,
    db: AsyncSession = Depends(get_db),
) -> ResourcePropagateResponse:
    result = await resource_permission_service.propagate(
        db, identity,
        resource_type=resource_type,
        source_resource_id=source_resource_id,
        target_resource_ids=body.target_resource_ids,
        mode=body.mode,
    )
    return ResourcePropagateResponse.model_validate(result)
