"""Эндпоинты управления пользовательскими группами."""

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from src.dependencies.auth import AnyAdmin, CurrentUserIdentity
from src.dependencies.db import get_db
from src.utils.pagination import PaginationParams, pagination_params
from src.schemas.common import OkResponse
from src.schemas.groups import (
    BotMemberAddRequest, BotMemberResponse,
    GroupCreate, GroupResponse, GroupRoleAssignRequest, GroupRoleResponse,
    GroupServiceAccessResponse, GroupServiceGrantRequest, GroupUpdate,
    MemberAddRequest, MemberResponse,
)
from src.services import group_service

router = APIRouter()

# ── CRUD групп ───────────────────────────────────────────────────────────────

groups_router = APIRouter(prefix="/groups")


@groups_router.get(
    "",
    response_model=list[GroupResponse],
    summary="Список групп",
    description="account_admin видит все. department_admin/обычный юзер — только свои.",
)
async def list_groups(
    request: Request,
    response: Response,
    identity: CurrentUserIdentity,
    pagination: PaginationParams = Depends(pagination_params),
    db: AsyncSession = Depends(get_db),
) -> list[GroupResponse]:
    """Список групп с учётом scope-а смотрящего.

    Пагинация:
        Query-параметры `limit`/`offset`; общее число — в `X-Total-Count`.
    """
    items, total = await group_service.list_groups(
        db, identity, pagination=pagination,
        request_id=getattr(request.state, "request_id", None),
    )
    response.headers["X-Total-Count"] = str(total)
    return items


@groups_router.post(
    "",
    response_model=GroupResponse,
    status_code=201,
    summary="Создать группу",
    description="Группа всегда привязана к отделу. department_admin может создавать только в своём.",
)
async def create_group(
    body: GroupCreate, request: Request, identity: CurrentUserIdentity, db: AsyncSession = Depends(get_db),
) -> GroupResponse:
    """Создать группу.

    Доступ:
        account_admin (любой отдел) или department_admin (только свой).

    Возможные ошибки:
        * `GROUP_ALREADY_EXISTS` (409) — внутри отдела имя уже занято.
        * `DEPARTMENT_ACCESS_DENIED` (403) — cross-dept у department_admin.
        * `DEPARTMENT_NOT_FOUND` (404).
    """
    return await group_service.create_group(
        db, identity, body.department_id, body.name, body.description,
        request_id=getattr(request.state, "request_id", None),
    )


@groups_router.get(
    "/{group_id}",
    response_model=GroupResponse,
    summary="Получить группу",
    description="account_admin — любая группа. department_admin — только своего отдела.",
)
async def get_group(
    group_id: str, request: Request, identity: CurrentUserIdentity, db: AsyncSession = Depends(get_db),
) -> GroupResponse:
    """Одиночная группа по id.

    Доступ:
        account_admin (любая группа) или department_admin (только своего отдела).

    Возможные ошибки:
        * `GROUP_NOT_FOUND` (404).
    """
    return await group_service.get_group(
        db, identity, group_id,
        request_id=getattr(request.state, "request_id", None),
    )


@groups_router.patch(
    "/{group_id}",
    response_model=GroupResponse,
    summary="Обновить группу",
)
async def update_group(
    group_id: str, body: GroupUpdate, request: Request,
    identity: AnyAdmin, db: AsyncSession = Depends(get_db),
) -> GroupResponse:
    """Patch name/description группы.

    Доступ:
        account_admin (любая группа) или department_admin (только своего отдела).
    """
    return await group_service.update_group(
        db, identity, group_id, body.name, body.description,
        request_id=getattr(request.state, "request_id", None),
    )


@groups_router.delete(
    "/{group_id}",
    response_model=OkResponse,
    summary="Удалить группу",
    description="Каскадно убирает всех members и group_service_roles/access.",
)
async def delete_group(
    group_id: str, request: Request, identity: AnyAdmin, db: AsyncSession = Depends(get_db),
) -> OkResponse:
    """Снести группу (вместе с членством и ролями).

    Доступ:
        account_admin (любая группа) или department_admin (только своего отдела).
    """
    await group_service.delete_group(db, identity, group_id,
                                     request_id=getattr(request.state, "request_id", None))
    return OkResponse()


# ── Members ───────────────────────────────────────────────────────────────────

@groups_router.get(
    "/{group_id}/members",
    response_model=list[MemberResponse],
    summary="Состав группы",
)
async def list_members(
    group_id: str, request: Request, identity: CurrentUserIdentity, db: AsyncSession = Depends(get_db),
) -> list[MemberResponse]:
    """Юзеры в группе.

    Доступ:
        account_admin или department_admin своего отдела.
    """
    return await group_service.list_members(db, identity, group_id,
                                            request_id=getattr(request.state, "request_id", None))


@groups_router.post(
    "/{group_id}/members",
    response_model=MemberResponse,
    status_code=201,
    summary="Добавить юзера в группу",
    description="Юзер и группа должны быть в одном отделе (GROUP_DEPARTMENT_MISMATCH guard).",
)
async def add_member(
    group_id: str, body: MemberAddRequest, request: Request,
    identity: AnyAdmin, db: AsyncSession = Depends(get_db),
) -> MemberResponse:
    """Добавить membership.

    Доступ:
        account_admin или department_admin своего отдела.

    Возможные ошибки:
        * `GROUP_DEPARTMENT_MISMATCH` (403) — юзер из другого отдела.
    """
    return await group_service.add_member(db, identity, group_id, body.user_id,
                                          request_id=getattr(request.state, "request_id", None))


@groups_router.delete(
    "/{group_id}/members/{user_id}",
    response_model=OkResponse,
    summary="Убрать юзера из группы",
)
async def remove_member(
    group_id: str, user_id: str, request: Request,
    identity: AnyAdmin, db: AsyncSession = Depends(get_db),
) -> OkResponse:
    """Удалить membership."""
    await group_service.remove_member(db, identity, group_id, user_id,
                                      request_id=getattr(request.state, "request_id", None))
    return OkResponse()


# ── Bot members ─────────────────────────────────────────────────────────────────

@groups_router.get(
    "/{group_id}/bots",
    response_model=list[BotMemberResponse],
    summary="Боты в группе",
)
async def list_bot_members(
    group_id: str, request: Request, identity: CurrentUserIdentity, db: AsyncSession = Depends(get_db),
) -> list[BotMemberResponse]:
    """Боты, состоящие в группе.

    Доступ:
        account_admin или department_admin своего отдела.
    """
    return await group_service.list_bot_members(db, identity, group_id,
                                                request_id=getattr(request.state, "request_id", None))


@groups_router.post(
    "/{group_id}/bots",
    response_model=BotMemberResponse,
    status_code=201,
    summary="Добавить бота в группу",
    description="Бот и группа должны быть в одном отделе (GROUP_DEPARTMENT_MISMATCH guard). Бот наследует роли группы.",
)
async def add_bot_member(
    group_id: str, body: BotMemberAddRequest, request: Request,
    identity: AnyAdmin, db: AsyncSession = Depends(get_db),
) -> BotMemberResponse:
    """Добавить бота в группу.

    Доступ:
        account_admin или department_admin своего отдела.

    Возможные ошибки:
        * `GROUP_DEPARTMENT_MISMATCH` (403) — бот из другого отдела.
    """
    return await group_service.add_bot_member(db, identity, group_id, body.bot_id,
                                             request_id=getattr(request.state, "request_id", None))


@groups_router.delete(
    "/{group_id}/bots/{bot_id}",
    response_model=OkResponse,
    summary="Убрать бота из группы",
)
async def remove_bot_member(
    group_id: str, bot_id: str, request: Request,
    identity: AnyAdmin, db: AsyncSession = Depends(get_db),
) -> OkResponse:
    """Удалить bot-membership. Роли группы перестают наследоваться (live)."""
    await group_service.remove_bot_member(db, identity, group_id, bot_id,
                                         request_id=getattr(request.state, "request_id", None))
    return OkResponse()


# ── Service access группы ─────────────────────────────────────────────────────

@groups_router.get(
    "/{group_id}/services",
    response_model=list[GroupServiceAccessResponse],
    summary="Сервисы, к которым группа имеет access",
)
async def list_group_services(
    group_id: str, request: Request, identity: CurrentUserIdentity, db: AsyncSession = Depends(get_db),
) -> list[GroupServiceAccessResponse]:
    """Service-access группы.

    Доступ:
        account_admin или department_admin своего отдела.
    """
    return await group_service.list_group_services(db, identity, group_id,
                                                   request_id=getattr(request.state, "request_id", None))


@groups_router.post(
    "/{group_id}/services",
    response_model=GroupServiceAccessResponse,
    status_code=201,
    summary="Дать группе access к сервису",
    description="Сервис должен быть в `allowed_services` отдела (иначе SERVICE_NOT_ALLOWED_FOR_DEPARTMENT).",
)
async def grant_service(
    group_id: str, body: GroupServiceGrantRequest, request: Request,
    identity: CurrentUserIdentity, db: AsyncSession = Depends(get_db),
) -> GroupServiceAccessResponse:
    """Выдать group access к сервису.

    Что делает:
        Создаёт `GroupServiceAccess`. Через эту связку effective view
        агрегирует разрешения для всех members группы.

    Доступ:
        account_admin или department_admin своего отдела.
    """
    return await group_service.grant_service_to_group(
        db, identity, group_id, body.service_name,
        request_id=getattr(request.state, "request_id", None),
    )


@groups_router.delete(
    "/{group_id}/services/{service_name}",
    response_model=OkResponse,
    summary="Отозвать access группы к сервису",
)
async def revoke_service(
    group_id: str, service_name: str, request: Request,
    identity: CurrentUserIdentity, db: AsyncSession = Depends(get_db),
) -> OkResponse:
    """Снять group access. group_service_roles тоже отбрасываются на INTERSECT."""
    await group_service.revoke_service_from_group(db, identity, group_id, service_name,
                                                  request_id=getattr(request.state, "request_id", None))
    return OkResponse()


# ── Group service roles ───────────────────────────────────────────────────────

@groups_router.get(
    "/{group_id}/roles",
    response_model=list[GroupRoleResponse],
    summary="Роли группы по сервисам",
)
async def list_group_roles(
    group_id: str, request: Request, identity: CurrentUserIdentity, db: AsyncSession = Depends(get_db),
) -> list[GroupRoleResponse]:
    """Service-роли группы.

    Доступ:
        account_admin или department_admin своего отдела.
    """
    return await group_service.list_group_roles(db, identity, group_id,
                                                request_id=getattr(request.state, "request_id", None))


@groups_router.post(
    "/{group_id}/roles",
    response_model=GroupRoleResponse,
    status_code=201,
    summary="Назначить группе роли для сервиса",
    description="Replace-семантика. Сервис должен быть в group_service_access.",
)
async def assign_group_roles(
    group_id: str, body: GroupRoleAssignRequest, request: Request,
    identity: CurrentUserIdentity, db: AsyncSession = Depends(get_db),
) -> GroupRoleResponse:
    """Replace ролей группы для сервиса.

    Возможные ошибки:
        * `GROUP_SERVICE_ACCESS_REQUIRED` (403) — нет group_service_access.
        * `SERVICE_NOT_FOUND` (404) — нет такого `PlatformService`.
        * `INVALID_SERVICE_ROLE` (422) — нет такого `ServiceRoleDefinition` в
          `(department, service)`.
    """
    return await group_service.assign_group_roles(
        db, identity, group_id, body.service_name, body.roles,
        request_id=getattr(request.state, "request_id", None),
    )


@groups_router.delete(
    "/{group_id}/roles/{service_name}",
    response_model=OkResponse,
    summary="Снять у группы все роли для сервиса",
)
async def revoke_group_roles(
    group_id: str, service_name: str, request: Request,
    identity: CurrentUserIdentity, db: AsyncSession = Depends(get_db),
) -> OkResponse:
    """Удалить все `GroupServiceRole` для пары (group, service)."""
    await group_service.revoke_group_roles(db, identity, group_id, service_name,
                                           request_id=getattr(request.state, "request_id", None))
    return OkResponse()


router.include_router(groups_router, tags=["groups"])
