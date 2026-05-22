"""Эндпоинты регистра платформенных сервисов."""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.dependencies.auth import AccountAdmin
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
    identity: AccountAdmin,
    db: AsyncSession = Depends(get_db),
) -> list[ServiceResponse]:
    """Список зарегистрированных сервисов.

    Доступ:
        Только account_admin.
    """
    return await platform_service_service.list_services(
        db,
        actor_id=identity.user_id,
        request_id=getattr(request.state, "request_id", None),
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
        * `SERVICE_NAME_TAKEN` (409) — такой `service_name` уже есть.
    """
    return await platform_service_service.create_service(
        db=db,
        actor_id=identity.user_id,
        service_name=body.service_name,
        display_name=body.display_name,
        description=body.description,
        request_id=getattr(request.state, "request_id", None),
    )


@router.delete(
    "/{service_name}",
    response_model=OkResponse,
    summary="Удалить сервис из регистра",
    description="Сервис не должен иметь активных department_access — иначе ошибка.",
)
async def delete_service(
    service_name: str,
    request: Request,
    identity: AccountAdmin,
    db: AsyncSession = Depends(get_db),
) -> OkResponse:
    """Снять сервис с регистрации.

    Доступ:
        Только account_admin.

    Возможные ошибки:
        * `SERVICE_HAS_DEPENDENCIES` (409) — есть активные DepartmentServiceAccess.
    """
    await platform_service_service.delete_service(
        db=db,
        actor_id=identity.user_id,
        service_name=service_name,
        request_id=getattr(request.state, "request_id", None),
    )
    return OkResponse()
