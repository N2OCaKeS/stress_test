"""Эндпоинты управления отделами и их доступом к платформенным сервисам."""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.dependencies.auth import AccountAdmin, AnyAdmin
from src.dependencies.db import get_db
from src.schemas.common import OkResponse
from src.schemas.departments import (
    DepartmentCreate,
    DepartmentResponse,
    DepartmentUpdateRequest,
    GrantServiceAccessRequest,
    HardDeleteDepartmentRequest,
    ServiceAccessResponse,
)
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


@router.patch(
    "/{department_id}",
    response_model=DepartmentResponse,
    summary="Обновить display_name / description отдела",
    description=(
        "Точечный апдейт: `display_name` и/или `description`. "
        "`name` (slug) — иммутабельный identity отдела для аудита и логов, "
        "через этот endpoint не меняется (схема его не принимает). "
        "Если оба поля отсутствуют — 422 `EMPTY_UPDATE`."
    ),
    responses={
        404: {"description": "DEPARTMENT_NOT_FOUND — отдела нет."},
        422: {"description": "EMPTY_UPDATE — оба поля не переданы."},
        403: {"description": "ROLE_REQUIRED — нужен account_admin."},
    },
)
async def update_department(
    department_id: str,
    body: DepartmentUpdateRequest,
    request: Request,
    identity: AccountAdmin,
    db: AsyncSession = Depends(get_db),
) -> DepartmentResponse:
    """Обновить редактируемые поля отдела.

    Доступ:
        Только `account_admin`. `department_admin` сюда не пускается — это
        соответствует существующей модели (dep_admin рулит ролями и юзерами
        внутри своего отдела, но не метаданными самого отдела).

    Возможные ошибки:
        * `DEPARTMENT_NOT_FOUND` (404).
        * `EMPTY_UPDATE` (422).
        * `ROLE_REQUIRED` (403).

    Audit:
        `department.updated` (INFO) с `changes={field: {old, new}}`.
    """
    # Guard на пустое тело здесь — чтобы клиент получил 422 ещё до похода в БД.
    if body.display_name is None and body.description is None:
        from src.core.exceptions import DomainValidationError
        raise DomainValidationError(
            error_code="EMPTY_UPDATE",
            message="At least one of display_name or description must be provided",
        )
    return await department_service.update_department(
        db=db,
        actor_id=identity.user_id,
        actor_username=identity.username,
        dept_id=department_id,
        body=body,
        request_id=getattr(request.state, "request_id", None),
    )


@router.get(
    "/{department_id}/services",
    response_model=list[str],
    summary="Список сервисов с активным grant'ом для отдела",
    description="Возвращает service_name'ы с активным DepartmentServiceAccess.",
)
async def list_department_services(
    department_id: str,
    request: Request,
    identity: AnyAdmin,
    db: AsyncSession = Depends(get_db),
) -> list[str]:
    """Список service_name с активным grant'ом для отдела.

    Доступ: любой админ (`AnyAdmin`). dep_admin'у scope не ограничивается
    на этом endpoint'е — read-only листинг своего же отдела безопасен.

    Возможные ошибки:
        * `DEPARTMENT_NOT_FOUND` (404).
    """
    return await department_service.list_department_services(
        db=db, department_id=department_id
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


@router.delete(
    "/{department_id}",
    response_model=OkResponse,
    summary="Hard-delete отдела (с указанием причины)",
    description=(
        "Жёсткое удаление отдела: row в `departments` сносится физически. "
        "CASCADE-FK уносят `DepartmentServiceAccess`, `ServiceRoleDefinition`, "
        "`UserGroup` (с её membership'ами и role-bindings), "
        "`DepartmentDockerRegistry`. Боты отдела и oauth_clients'ы (RESTRICT-FK) "
        "удаляются явно ДО dept-row'а (ORM-cascade уносит bot tokens / "
        "service roles / group memberships). "
        "После commit'а secret_service получает best-effort notify "
        "(`/internal/lifecycle/dept-deleted`), который блокирует cred'ы, "
        "принадлежащие отделу, и каскадно снимает DeptGrant'ы / RoleACL, "
        "где dept — recipient."
    ),
    responses={
        404: {"description": "DEPARTMENT_NOT_FOUND — отдела нет."},
        422: {"description": "USERS_REMAIN_IN_DEPT — в отделе остались активные юзеры, сначала их перевести или удалить."},
        403: {"description": "ROLE_REQUIRED — нужен account_admin."},
    },
)
async def hard_delete_department(
    department_id: str,
    body: HardDeleteDepartmentRequest,
    request: Request,
    identity: AccountAdmin,
    db: AsyncSession = Depends(get_db),
) -> OkResponse:
    """Hard-delete отдела.

    Доступ:
        Только `account_admin`.

    Защита:
        Запрет если в отделе остался хотя бы один активный юзер
        (`USERS_REMAIN_IN_DEPT` 422) — сначала перевести их в другой
        отдел через `PATCH /users/{id}` либо снести каждого hard-delete'ом.

    Возможные ошибки:
        * `DEPARTMENT_NOT_FOUND` (404).
        * `USERS_REMAIN_IN_DEPT` (422).
        * `ROLE_REQUIRED` (403) — не account_admin.

    Audit:
        `department.hard_deleted` (CRITICAL) с `reason`,
        `bots_deleted`, `bot_tokens_revoked`, `oauth_clients_deleted`.
    """
    await department_service.hard_delete_department(
        db=db,
        actor_id=identity.user_id,
        actor_username=identity.username,
        department_id=department_id,
        reason=body.reason,
        request_id=getattr(request.state, "request_id", None),
    )
    return OkResponse()
