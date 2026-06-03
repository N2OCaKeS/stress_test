"""Эндпоинты управления `ServiceRoleDefinition` (per-department scope)."""

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.dependencies.auth import CurrentUserIdentity
from src.dependencies.db import get_db
from src.schemas.common import OkResponse
from src.schemas.service_roles import (
    ServiceRoleCreate,
    ServiceRoleResponse,
    ServiceRoleUpdate,
)
from src.services import service_role_service


class BulkRoleRequest(BaseModel):
    """Тело bulk assign/revoke — список user_id, которым назначаем/у которых снимаем роль."""
    user_ids: list[str]


router = APIRouter(
    prefix="/departments/{department_id}/services/{service_name}/roles"
)


@router.get(
    "",
    response_model=list[ServiceRoleResponse],
    summary="Список определений ролей для (отдел, сервис)",
    description="Возвращает все `ServiceRoleDefinition` в scope (dept, service).",
)
async def list_roles(
    department_id: str,
    service_name: str,
    request: Request,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> list[ServiceRoleResponse]:
    """Список ролей в scope (отдел × сервис).

    Доступ:
        account_admin / department_admin своего отдела / юзер с access к сервису.
    """
    return await service_role_service.list_roles(
        db=db,
        identity=identity,
        department_id=department_id,
        service_name=service_name,
        request_id=getattr(request.state, "request_id", None),
    )


@router.post(
    "",
    response_model=ServiceRoleResponse,
    status_code=201,
    summary="Создать определение роли",
    description="Уникальность по `(department_id, service_name, role_name)`. Системная `admin` защищена `is_system`.",
)
async def create_role(
    department_id: str,
    service_name: str,
    body: ServiceRoleCreate,
    request: Request,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> ServiceRoleResponse:
    """Создать новое `ServiceRoleDefinition`.

    Что делает:
        Регистрирует роль в scope (отдел, сервис). После этого юзерам/ботам/
        группам можно её назначать.

    Возможные ошибки:
        * `SERVICE_ROLE_ALREADY_EXISTS` (409) — уже есть такая в scope.
        * `SERVICE_NOT_GRANTED_FOR_DEPARTMENT` (403) — у отдела нет access к сервису.
        * `SERVICE_ROLE_MGMT_FORBIDDEN` (403) — у actor'а нет прав на управление
          ролями в `(department, service)`.
    """
    return await service_role_service.create_role(
        db=db,
        identity=identity,
        department_id=department_id,
        service_name=service_name,
        role_name=body.role_name,
        display_name=body.display_name,
        description=body.description,
        request_id=getattr(request.state, "request_id", None),
    )


@router.patch(
    "/{role_name}",
    response_model=ServiceRoleResponse,
    summary="Обновить display_name/description роли",
)
async def update_role(
    department_id: str,
    service_name: str,
    role_name: str,
    body: ServiceRoleUpdate,
    request: Request,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> ServiceRoleResponse:
    """Patch определения роли. Меняются только display_name и description."""
    return await service_role_service.update_role(
        db=db,
        identity=identity,
        department_id=department_id,
        service_name=service_name,
        role_name=role_name,
        display_name=body.display_name,
        description=body.description,
        request_id=getattr(request.state, "request_id", None),
    )


@router.delete(
    "/{role_name}",
    response_model=OkResponse,
    summary="Удалить определение роли",
    description="Системная роль (`is_system=True`) не удаляется.",
)
async def delete_role(
    department_id: str,
    service_name: str,
    role_name: str,
    request: Request,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> OkResponse:
    """Удалить ServiceRoleDefinition.

    Возможные ошибки:
        * `SERVICE_ROLE_SYSTEM_LOCKED` (403) — пытаемся снести системную (`is_system=True`).
        * `SERVICE_ROLE_NOT_FOUND` (404).
    """
    await service_role_service.delete_role(
        db=db,
        identity=identity,
        department_id=department_id,
        service_name=service_name,
        role_name=role_name,
        request_id=getattr(request.state, "request_id", None),
    )
    return OkResponse()


@router.post(
    "/{role_name}/assign",
    response_model=OkResponse,
    summary="Bulk-назначение роли списку юзеров",
    description="За один запрос выдаёт роль `role_name` всем юзерам из `user_ids`.",
)
async def bulk_assign(
    department_id: str,
    service_name: str,
    role_name: str,
    body: BulkRoleRequest,
    request: Request,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> OkResponse:
    """Bulk-выдать роль.

    Что делает:
        Идиомпотентно создаёт `UserServiceRole` для каждого user_id из
        списка. Юзеры из чужого отдела отбрасываются.
    """
    await service_role_service.bulk_assign(
        db=db,
        identity=identity,
        department_id=department_id,
        service_name=service_name,
        role_name=role_name,
        user_ids=body.user_ids,
        request_id=getattr(request.state, "request_id", None),
    )
    return OkResponse()


@router.post(
    "/{role_name}/revoke",
    response_model=OkResponse,
    summary="Bulk-снятие роли с группы юзеров",
)
async def bulk_revoke(
    department_id: str,
    service_name: str,
    role_name: str,
    body: BulkRoleRequest,
    request: Request,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> OkResponse:
    """Bulk-снять роль со списка юзеров."""
    await service_role_service.bulk_revoke(
        db=db,
        identity=identity,
        department_id=department_id,
        service_name=service_name,
        role_name=role_name,
        user_ids=body.user_ids,
        request_id=getattr(request.state, "request_id", None),
    )
    return OkResponse()
