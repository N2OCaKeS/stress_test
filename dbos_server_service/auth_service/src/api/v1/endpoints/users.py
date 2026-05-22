"""Эндпоинты CRUD пользователей и управления ролями/группами."""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.dependencies.auth import AccountAdmin, AnyAdmin, CurrentUserIdentity
from src.dependencies.db import get_db
from src.schemas.common import OkResponse
from src.schemas.groups import UserGroupsResponse
from src.schemas.users import (
    AddUserToGroupRequest,
    AssignRolesRequest,
    BanRequest,
    ResetPasswordRequest,
    UserCreate,
    UserPermissionsResponse,
    UserResponse,
    UserUpdate,
)
from src.services import group_service, user_service

router = APIRouter(prefix="/users")


@router.get(
    "",
    response_model=list[UserResponse],
    summary="Список всех пользователей (глобально)",
    description="Только для account_admin. Видит юзеров всех отделов.",
)
async def list_users(
    request: Request,
    identity: AccountAdmin,
    db: AsyncSession = Depends(get_db),
) -> list[UserResponse]:
    """Глобальный список юзеров.

    Доступ:
        Только account_admin. Department_admin использует
        `/users/department/{department_id}`.
    """
    return await user_service.list_users(
        db=db,
        actor_id=identity.user_id,
        request_id=getattr(request.state, "request_id", None),
    )


@router.get(
    "/department/{department_id}",
    response_model=list[UserResponse],
    summary="Юзеры конкретного отдела",
    description="Account_admin — любой отдел. Department_admin — только свой.",
)
async def list_users_by_department(
    department_id: str,
    request: Request,
    identity: AnyAdmin,
    db: AsyncSession = Depends(get_db),
) -> list[UserResponse]:
    """Юзеры одного отдела.

    Доступ:
        * account_admin — любой отдел;
        * department_admin — только свой (иначе 404, чтобы не было ID-oracle).

    Возможные ошибки:
        * `DEPARTMENT_NOT_FOUND` (404) — нет такого отдела, либо
          department_admin попросил чужой.
    """
    return await user_service.list_users_by_department(
        db=db,
        actor_id=identity.user_id,
        actor_role=identity.platform_role,
        department_id=department_id,
        request_id=getattr(request.state, "request_id", None),
    )


@router.post(
    "",
    response_model=UserResponse,
    status_code=201,
    summary="Создать пользователя",
    description="Создаёт юзера с паролем (хэшируется Argon2id). Опциональные initial_roles прикручивают service-роли сразу.",
    response_description="Созданный юзер.",
    responses={
        409: {"description": "username уже занят."},
    },
)
async def create_user(
    body: UserCreate,
    request: Request,
    identity: AnyAdmin,
    db: AsyncSession = Depends(get_db),
) -> UserResponse:
    """Создать нового юзера.

    Что делает:
        Создаёт `User` + хэш пароля (Argon2id, OWASP 2023 params). Если
        переданы `initial_roles` — выдаёт service-роли в одной транзакции.

    Доступ:
        * account_admin — может создавать в любом отделе, в т.ч. account_admin
          (без department_id);
        * department_admin — только в своём отделе, и не account_admin.

    Возможные ошибки:
        * `USERNAME_TAKEN` (409) — username уже занят.
        * `DEPARTMENT_NOT_FOUND` (404) — указанный department_id не найден.
        * `PERMISSION_DENIED` (403) — department_admin пытается создать в
          чужом отделе или создать account_admin.
    """
    return await user_service.create_user(
        db=db,
        actor_id=identity.user_id,
        actor_role=identity.platform_role,
        username=body.username,
        password=body.password,
        department_id=body.department_id,
        email=body.email,
        platform_role=body.platform_role,
        initial_roles=body.initial_roles,
        request_id=getattr(request.state, "request_id", None),
    )


@router.patch(
    "/{user_id}",
    response_model=UserResponse,
    summary="Обновить юзера",
    description="Частичный update — отправляй только те поля, которые меняешь.",
)
async def update_user(
    user_id: str,
    body: UserUpdate,
    request: Request,
    identity: AnyAdmin,
    db: AsyncSession = Depends(get_db),
) -> UserResponse:
    """Изменить поля юзера.

    Что делает:
        Patch'ит `User`. `status` ограничен enum'ом `UserStatus` (иначе
        admin мог положить мусор и поломать сравнения в introspect).

    Доступ:
        * account_admin — любой юзер;
        * department_admin — только в своём отделе, не account_admin.

    Возможные ошибки:
        * `USER_NOT_FOUND` (404).
        * `PERMISSION_DENIED` (403) — выход за scope department_admin'а.
    """
    return await user_service.update_user(
        db=db,
        actor_id=identity.user_id,
        actor_role=identity.platform_role,
        user_id=user_id,
        updates=body.model_dump(exclude_none=True),
        request_id=getattr(request.state, "request_id", None),
    )


@router.post(
    "/{user_id}/roles",
    summary="Назначить service-роли юзеру",
    description="Перезаписывает роли юзера для указанного сервиса. Replace-семантика, не append.",
)
async def assign_roles(
    user_id: str,
    body: AssignRolesRequest,
    request: Request,
    identity: AnyAdmin,
    db: AsyncSession = Depends(get_db),
) -> OkResponse:
    """Прямые service-роли для юзера.

    Что делает:
        Replace-семантика: список `roles` для `service_name` заменяет
        текущий набор. Чтобы убрать все роли — передай пустой список.

    Доступ:
        account_admin или department_admin своего отдела. Сервис должен быть
        в `allowed_services` отдела.

    Возможные ошибки:
        * `SERVICE_ACCESS_DENIED` (403) — отдел не имеет access к сервису.
        * `ROLE_NOT_FOUND` (404) — нет такого `ServiceRoleDefinition`.
    """
    await user_service.assign_roles(
        db=db,
        actor_id=identity.user_id,
        actor_role=identity.platform_role,
        user_id=user_id,
        service_name=body.service_name,
        roles=body.roles,
        request_id=getattr(request.state, "request_id", None),
    )
    return OkResponse()


@router.post(
    "/{user_id}/reset-password",
    response_model=OkResponse,
    summary="Сбросить пароль юзеру",
    description="Перезаписывает пароль (Argon2id хэш). Все активные сессии юзера НЕ ревокаются — только пароль.",
)
async def reset_password(
    user_id: str,
    body: ResetPasswordRequest,
    request: Request,
    identity: AnyAdmin,
    db: AsyncSession = Depends(get_db),
) -> OkResponse:
    """Сбросить пароль за юзера.

    Что делает:
        Записывает новый хэш Argon2id. Минимум 8 символов (валидируется
        pydantic'ом). Существующие refresh не убиваются — отдельная задача.

    Доступ:
        account_admin (любой юзер) или department_admin (только свой отдел).

    Возможные ошибки:
        * `USER_NOT_FOUND` (404).
        * `PERMISSION_DENIED` (403) — cross-dept у department_admin.
    """
    await user_service.reset_password(
        db=db,
        actor_id=identity.user_id,
        user_id=user_id,
        new_password=body.new_password,
        request_id=getattr(request.state, "request_id", None),
    )
    return OkResponse()


@router.post(
    "/{user_id}/ban",
    response_model=OkResponse,
    summary="Забанить юзера",
    description="Permanent или temporary. Убивает все сессии + PAT + bot-токены owned-ботов.",
)
async def ban_user(
    user_id: str,
    body: BanRequest,
    request: Request,
    identity: AccountAdmin,
    db: AsyncSession = Depends(get_db),
) -> OkResponse:
    """Забанить юзера.

    Что делает:
        Создаёт `Ban` (permanent/temporary), revoke'ит все активные сессии
        юзера + все PAT + bot-токены ботов, которыми он владеет.
        `temporary` обязательно требует `expires_at` в будущем.

    Доступ:
        Только account_admin.

    Возможные ошибки:
        * `USER_NOT_FOUND` (404).
        * 422 — `expires_at` в прошлом, permanent с `expires_at`, temporary
          без `expires_at`.
    """
    await user_service.ban_user(
        db=db,
        actor_id=identity.user_id,
        user_id=user_id,
        ban_type=body.ban_type,
        reason=body.reason,
        expires_at=body.expires_at,
        request_id=getattr(request.state, "request_id", None),
    )
    return OkResponse()


@router.get(
    "/{user_id}/groups",
    response_model=list[UserGroupsResponse],
    summary="Группы, в которых состоит юзер",
    description="Кто что видит зависит от роли смотрящего — см. описание endpoint'а.",
)
async def list_user_groups(
    user_id: str,
    request: Request,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> list[UserGroupsResponse]:
    """Группы юзера.

    Доступ:
        * account_admin — любой юзер (cross-dept by design).
        * department_admin — только юзер своего отдела (cross-dept → 404,
          не 403, чтобы избежать ID-enum oracle).
        * regular user — только себя.

    Связано:
        Симметрично `/users/department/{id}` по guard-поведению.
    """
    return await group_service.list_user_groups(
        db=db,
        identity=identity,
        user_id=user_id,
        request_id=getattr(request.state, "request_id", None),
    )


@router.get(
    "/{user_id}/permissions",
    response_model=UserPermissionsResponse,
    summary="Полный снимок прав юзера",
    description="Прямые роли + группы (со всеми их service-access и ролями) + effective view + статус.",
)
async def get_user_permissions(
    user_id: str,
    request: Request,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> UserPermissionsResponse:
    """Полный снимок прав пользователя.

    Что делает:
        Возвращает три слоя: (a) прямые `UserServiceRole`, (b) группы со
        списком service_accesses и service_roles, (c) effective view —
        merged + INTERSECT с `allowed_services` через
        `collect_user_permissions`. Плюс platform_role и статус.

    Доступ:
        * account_admin — любой юзер;
        * department_admin — только юзер своего отдела;
        * сам юзер — может смотреть себя;
        * иначе — 403.

    Возможные ошибки:
        * `USER_NOT_FOUND` (404).
        * `PERMISSION_DENIED` (403).
    """
    return await user_service.get_user_permissions(
        db=db,
        user_id=user_id,
        identity=identity,
        request_id=getattr(request.state, "request_id", None),
    )


@router.post(
    "/{user_id}/groups",
    response_model=OkResponse,
    status_code=201,
    summary="Добавить юзера в группу",
    description="Группа должна быть в том же отделе, что и юзер (GROUP_DEPARTMENT_MISMATCH guard).",
)
async def add_user_to_group(
    user_id: str,
    body: AddUserToGroupRequest,
    request: Request,
    identity: AnyAdmin,
    db: AsyncSession = Depends(get_db),
) -> OkResponse:
    """Записать юзера в группу.

    Что делает:
        Создаёт `UserGroupMembership`. Юзер и группа должны быть в одном
        отделе (инвариант, проверяется явно).

    Доступ:
        account_admin (любой отдел) или department_admin (только свой).

    Возможные ошибки:
        * `GROUP_NOT_FOUND` / `USER_NOT_FOUND` (404).
        * `GROUP_DEPARTMENT_MISMATCH` (400) — юзер и группа в разных отделах.
    """
    await group_service.add_member(
        db=db,
        identity=identity,
        group_id=body.group_id,
        user_id=user_id,
        request_id=getattr(request.state, "request_id", None),
    )
    return OkResponse()


@router.delete(
    "/{user_id}/groups/{group_id}",
    response_model=OkResponse,
    summary="Убрать юзера из группы",
)
async def remove_user_from_group(
    user_id: str,
    group_id: str,
    request: Request,
    identity: AnyAdmin,
    db: AsyncSession = Depends(get_db),
) -> OkResponse:
    """Удалить membership.

    Доступ:
        account_admin или department_admin своего отдела.
    """
    await group_service.remove_member(
        db=db,
        identity=identity,
        group_id=group_id,
        user_id=user_id,
        request_id=getattr(request.state, "request_id", None),
    )
    return OkResponse()


@router.post(
    "/{user_id}/unban",
    response_model=OkResponse,
    summary="Снять бан с юзера",
    description="Отзывает активный ban. Сессии не восстанавливает — юзеру надо логиниться заново.",
)
async def unban_user(
    user_id: str,
    request: Request,
    identity: AccountAdmin,
    db: AsyncSession = Depends(get_db),
) -> OkResponse:
    """Снять бан.

    Доступ:
        Только account_admin (симметрично ban-у).

    Возможные ошибки:
        * `USER_NOT_FOUND` (404).
        * `NOT_BANNED` (400) — у юзера нет активного бана.
    """
    await user_service.unban_user(
        db=db,
        actor_id=identity.user_id,
        user_id=user_id,
        request_id=getattr(request.state, "request_id", None),
    )
    return OkResponse()
