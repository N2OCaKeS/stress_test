"""Эндпоинты регистра платформенных сервисов."""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.dependencies.auth import AccountAdmin, AnyAdmin
from src.core.constants import PlatformRole
from src.dependencies.db import get_db
from src.schemas.common import OkResponse
from src.schemas.services import ServiceCreate, ServiceResponse
from src.services import platform_service_service

router = APIRouter(prefix="/services")


@router.get(
    "",
    response_model=list[ServiceResponse],
    summary="Список платформенных сервисов",
    description="Регистр всех известных сервисов платформы.",
)
async def list_services(
    request: Request,
    identity: AnyAdmin,
    db: AsyncSession = Depends(get_db),
) -> list[ServiceResponse]:
    """Список зарегистрированных сервисов.

    Доступ:
        account_admin видит весь регистр. department_admin — только сервисы,
        к которым подключён его отдел (нужно, чтобы выбрать сервис при
        назначении ролей юзеру). Создание/удаление — по-прежнему account_admin.
    """
    is_account_admin = identity.platform_role == PlatformRole.ACCOUNT_ADMIN
    return await platform_service_service.list_services(
        db,
        actor_id=identity.user_id,
        request_id=getattr(request.state, "request_id", None),
        department_id=identity.department_id,
        all_services=is_account_admin,
    )


@router.post(
    "",
    response_model=ServiceResponse,
    status_code=201,
    summary="Зарегистрировать сервис",
    description="`service_name` — стабильный машинный ID (используется в ролях/access).",
)
async def create_service(
    body: ServiceCreate,
    request: Request,
    identity: AccountAdmin,
    db: AsyncSession = Depends(get_db),
) -> ServiceResponse:
    """Регистрация нового сервиса.

    Что делает:
        Создаёт запись `PlatformService`. После этого можно выдавать
        отделам access через `/departments/{id}/services`.

    Доступ:
        Только account_admin.

    Возможные ошибки:
        * `SERVICE_ALREADY_EXISTS` (409) — такой `service_name` уже есть.
    """
    return await platform_service_service.create_service(
        db=db,
        actor_id=identity.user_id,
        service_name=body.service_name,
        description=body.description,
        request_id=getattr(request.state, "request_id", None),
    )


@router.delete(
    "/{service_name}",
    response_model=OkResponse,
    summary="Удалить сервис из регистра",
    description="Каскадно деактивирует все DepartmentServiceAccess, UserServiceRole, BotServiceRole и ServiceRoleDefinition для этого сервиса.",
)
async def delete_service(
    service_name: str,
    request: Request,
    identity: AccountAdmin,
    db: AsyncSession = Depends(get_db),
) -> OkResponse:
    """Снять сервис с регистрации (cascade).

    Доступ:
        Только account_admin.

    Каскад:
        Деактивирует все `DepartmentServiceAccess`, `UserServiceRole`,
        `BotServiceRole` и `ServiceRoleDefinition` для сервиса. Identity-cache
        затронутых ботов инвалидируется. Audit `service.delete` несёт
        `cascade_revoked_department_access`, `cascade_deactivated_roles`,
        `affected_bot_count`.

    Возможные ошибки:
        * `SERVICE_NOT_FOUND` (404).
    """
    await platform_service_service.delete_service(
        db=db,
        actor_id=identity.user_id,
        service_name=service_name,
        request_id=getattr(request.state, "request_id", None),
    )
    return OkResponse()
