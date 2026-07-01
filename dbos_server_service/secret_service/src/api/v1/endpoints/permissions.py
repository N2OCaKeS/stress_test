"""Эндпоинты тип-wide матрицы прав secret_service — каталог / список / grant / revoke.

Право управлять матрицей несут `account_admin` (мета-админ любого отдела),
`department_admin` своего отдела и носитель сервисной роли `admin`
secret_service'а своего отдела. Полный rule-set — в
`services/permission_service.py`. Гейт делает сам сервис (не middleware):
identity резолвится через `get_identity` без `require_user_context`, чтобы
платформенный account_admin (без department-service-access) мог править
матрицу.
"""

from fastapi import APIRouter, Body, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.dependencies.auth import CurrentIdentity
from src.dependencies.db import get_db
from src.schemas.common import OkResponse
from src.schemas.permission import (
    CatalogEntity,
    PermissionDescribedResponse,
    PermissionGrant,
    PermissionListResponse,
    PermissionResponse,
)
from src.services import permission_service

router = APIRouter(prefix="/permissions", tags=["permissions"])


@router.get(
    "",
    response_model=PermissionListResponse,
    summary="Список записей entity_permissions",
    description=(
        "Матрица grants в envelope'е `{items, total, described}`. `role=<role>` "
        "сужает выдачу до одной роли; `describe=true` обогащает строки "
        "описаниями и флагом `sensitive`. Доступ: account_admin / "
        "department_admin своего отдела / сервисная роль admin своего отдела."
    ),
    responses={403: {"description": "PERMISSION_DENIED — нет права управлять матрицей."}},
)
async def list_permissions(
    identity: CurrentIdentity,
    role: str | None = Query(default=None, max_length=64),
    describe: bool = Query(default=False),
    db: AsyncSession = Depends(get_db),
) -> PermissionListResponse:
    rows = await permission_service.list_all(db, identity, role=role)
    if describe:
        items: list[PermissionResponse | PermissionDescribedResponse] = [
            PermissionDescribedResponse.model_validate(
                {**PermissionResponse.model_validate(r).model_dump(), **permission_service.describe_row(r)}
            )
            for r in rows
        ]
    else:
        items = [PermissionResponse.model_validate(r) for r in rows]
    return PermissionListResponse(items=items, total=len(items), described=describe)


@router.get(
    "/catalog",
    response_model=list[CatalogEntity],
    summary="Каталог сущностей и действий с описаниями",
    description=(
        "Read-only справочник: сущность `secret` и её действия с описаниями и "
        "флагом `sensitive`. Доступ: как у GET /permissions."
    ),
    responses={403: {"description": "PERMISSION_DENIED."}},
)
async def permissions_catalog(
    identity: CurrentIdentity,
    db: AsyncSession = Depends(get_db),
) -> list[CatalogEntity]:
    entities = await permission_service.get_catalog(db, identity)
    return [CatalogEntity.model_validate(e) for e in entities]


@router.get(
    "/{entity_type}",
    response_model=PermissionListResponse,
    summary="Grants для одного entity_type",
    description=(
        "То же, что GET /permissions, отфильтровано по `entity_type`. "
        "describe-обогащения нет (`described` всегда false). Неизвестный "
        "entity_type → 422 UNKNOWN_ENTITY_TYPE."
    ),
    responses={
        403: {"description": "PERMISSION_DENIED."},
        422: {"description": "UNKNOWN_ENTITY_TYPE."},
    },
)
async def list_permissions_for_entity(
    entity_type: str,
    identity: CurrentIdentity,
    db: AsyncSession = Depends(get_db),
) -> PermissionListResponse:
    rows = await permission_service.list_for_entity(db, identity, entity_type)
    items: list[PermissionResponse | PermissionDescribedResponse] = [
        PermissionResponse.model_validate(r) for r in rows
    ]
    return PermissionListResponse(items=items, total=len(items), described=False)


@router.put(
    "/{entity_type}/{role}/{action}",
    response_model=PermissionResponse,
    summary="Выдать `action` на `entity_type` для `role`",
    description=(
        "Идемпотентно. Body `{target_department_id}` опционально: caller обязан "
        "опустить поле или передать свой `department_id`, иначе 403 "
        "DEPARTMENT_ISOLATION. Системные роли guest/admin → 409. Невалидная "
        "пара (entity_type, action) → 422."
    ),
    responses={
        403: {"description": "PERMISSION_DENIED либо DEPARTMENT_ISOLATION."},
        409: {"description": "SYSTEM_ROLE_IMMUTABLE либо PERMISSION_ALREADY_EXISTS."},
        422: {"description": "INVALID_ACTION_FOR_ENTITY."},
    },
)
async def grant_permission(
    entity_type: str,
    role: str,
    action: str,
    identity: CurrentIdentity,
    body: PermissionGrant | None = Body(default=None),
    db: AsyncSession = Depends(get_db),
) -> PermissionResponse:
    target_dept = body.target_department_id if body is not None else None
    obj = await permission_service.grant_action(
        db, identity,
        entity_type=entity_type, role=role, action=action,
        target_department_id=target_dept,
    )
    return PermissionResponse.model_validate(obj)


@router.delete(
    "/{entity_type}/{role}/{action}",
    response_model=OkResponse,
    summary="Снять `action` на `entity_type` с `role`",
    description=(
        "Scope как у PUT, но через query-параметр `target_department_id`. "
        "Системные роли guest/admin → 409. Отсутствие строки → 404."
    ),
    responses={
        403: {"description": "PERMISSION_DENIED либо DEPARTMENT_ISOLATION."},
        404: {"description": "PERMISSION_NOT_FOUND."},
        409: {"description": "SYSTEM_ROLE_IMMUTABLE."},
    },
)
async def revoke_permission(
    entity_type: str,
    role: str,
    action: str,
    identity: CurrentIdentity,
    target_department_id: str | None = Query(default=None, max_length=64),
    db: AsyncSession = Depends(get_db),
) -> OkResponse:
    await permission_service.revoke_action(
        db, identity,
        entity_type=entity_type, role=role, action=action,
        target_department_id=target_department_id,
    )
    return OkResponse()
