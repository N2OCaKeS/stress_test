"""Эндпоинты управления отделами и их доступом к платформенным сервисам."""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.dependencies.auth import AccountAdmin
from src.dependencies.db import get_db
from src.schemas.common import OkResponse
from src.schemas.departments import DepartmentCreate, DepartmentResponse, GrantServiceAccessRequest, ServiceAccessResponse
from src.services import department_service

router = APIRouter(prefix="/departments")


@router.get(
    "",
    response_model=list[DepartmentResponse],
    summary="Список отделов",
    description="Только account_admin. Department_admin видит свой отдел через `/me`.",
)
async def list_departments(
    request: Request,
    identity: AccountAdmin,
    db: AsyncSession = Depends(get_db),
) -> list[DepartmentResponse]:
    """Все отделы платформы.

    Доступ:
        Только account_admin.
    """
    return await department_service.list_departments(
        db,
        actor_id=identity.user_id,
        request_id=getattr(request.state, "request_id", None),
    )


@router.post(
    "",
    response_model=DepartmentResponse,
    status_code=201,
    summary="Создать отдел",
    description="`name` — машинно-читаемый ID, `display_name` — человеческое название.",
)
async def create_department(
    body: DepartmentCreate,
    request: Request,
    identity: AccountAdmin,
    db: AsyncSession = Depends(get_db),
) -> DepartmentResponse:
    """Создать отдел.

    Доступ:
        Только account_admin.

    Возможные ошибки:
        * `DEPARTMENT_ALREADY_EXISTS` (409) — отдел с таким `name` уже есть.
    """
    return await department_service.create_department(
        db=db,
        actor_id=identity.user_id,
        name=body.name,
        display_name=body.display_name,
        request_id=getattr(request.state, "request_id", None),
    )


@router.post(
    "/{department_id}/services",
    response_model=ServiceAccessResponse,
    status_code=201,
    summary="Выдать отделу access к сервису",
    description="Без access юзеры/боты отдела не смогут получить роль для этого сервиса.",
)
async def grant_service_access(
    department_id: str,
    body: GrantServiceAccessRequest,
    request: Request,
    identity: AccountAdmin,
    db: AsyncSession = Depends(get_db),
) -> ServiceAccessResponse:
    """Дать отделу access к сервису.

    Что делает:
        Создаёт `DepartmentServiceAccess`. Без этой связки юзеры/боты
        отдела не смогут получить роль для сервиса (а если уже имели — те
        роли отбрасываются на INTERSECT в effective view).

    Доступ:
        Только account_admin.
    """
    return await department_service.grant_service_access(
        db=db,
        actor_id=identity.user_id,
        department_id=department_id,
        service_name=body.service_name,
        request_id=getattr(request.state, "request_id", None),
    )


@router.delete(
    "/{department_id}/services/{service_name}",
    response_model=OkResponse,
    summary="Отозвать у отдела access к сервису",
    description="После revoke роли юзеров/ботов на этот сервис формально остаются, но эффективно отбрасываются INTERSECT'ом.",
)
async def revoke_service_access(
    department_id: str,
    service_name: str,
    request: Request,
    identity: AccountAdmin,
    db: AsyncSession = Depends(get_db),
) -> OkResponse:
    """Отозвать access отдела к сервису.

    Доступ:
        Только account_admin.

    Связано:
        После revoke `_merge_permissions` пересекает direct/group роли с
        `dept_services ∪ group_services` — роли для отнятого сервиса
        выпадают из `IntrospectResponse.service_roles` автоматически.
    """
    await department_service.revoke_service_access(
        db=db,
        actor_id=identity.user_id,
        department_id=department_id,
        service_name=service_name,
        request_id=getattr(request.state, "request_id", None),
    )
    return OkResponse()
