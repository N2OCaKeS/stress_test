"""Эндпоинты CRUD пользователей и управления ролями/группами."""

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from src.dependencies.auth import AccountAdmin, AnyAdmin, CurrentUserIdentity
from src.dependencies.db import get_db
from src.utils.pagination import PaginationParams, pagination_params
from src.schemas.common import OkResponse
from src.schemas.groups import UserGroupsResponse
from src.schemas.users import (
    AddUserToGroupRequest,
    AssignRolesRequest,
    BanRequest,
    ResetPasswordRequest,
    RevokeSessionsRequest,
    RevokeSessionsResponse,
    SelfChangePasswordRequest,
    SessionsListResponse,
    UserCreate,
    UserPermissionsResponse,
    UserResponse,
    UserUpdate,
)
from src.services import group_service, user_service

router = APIRouter(prefix="/users")


# `/users/me/password` регистрируется до любых `/users/{user_id}/...`-роутов
# — FastAPI матчит по порядку регистрации, и без этого `me` улетал бы в
# `{user_id}`-парам с 404 USER_NOT_FOUND (или ещё хуже — в действие над
# юзером с буквальным id "me", если бы такой существовал).
@router.post(
    "/me/password",
    response_model=OkResponse,
    summary="Сменить собственный пароль",
    description=(
        "Self-reset пароля с подтверждением текущего пароля. После успеха все "
        "активные сессии юзера revoke'ятся (включая текущую — нужен повторный login); "
        "PAT остаются валидными."
    ),
    responses={
        401: {"description": "Старый пароль неверный (INVALID_OLD_PASSWORD)."},
        422: {"description": "Новый пароль не соответствует политике или совпадает со старым (SAME_PASSWORD)."},
        429: {"description": "Аккаунт залочен после серии неудачных подтверждений старого пароля."},
    },
)
async def change_own_password(
    body: SelfChangePasswordRequest,
    request: Request,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> OkResponse:
    """Сменить собственный пароль.

    Что делает:
        Проверяет `old_password` через Argon2id-verify. При неудаче — 401
        `INVALID_OLD_PASSWORD` + инкремент счётчика lockout'а (как в /login).
        При успехе — пишет новый Argon2id-хэш, revoke всех активных сессий
        юзера (refresh-токены становятся невалидными немедленно), сбрасывает
        identity-cache. PAT юзера НЕ отзываются — это отдельная identity,
        часто привязана к боту/CI.

    Доступ:
        Любой залогиненный юзер (Bearer). PAT/m2m не пропускаются
        (`require_user_context`), потому что у не-юзер-actor'а нет
        password_hash для verify.

    Возможные ошибки:
        * `INVALID_OLD_PASSWORD` (401) — старый пароль не совпал.
        * `SAME_PASSWORD` (422) — новый пароль совпадает со старым (verify
          поверх хэша, не строковое сравнение).
        * `ACCOUNT_TEMPORARILY_LOCKED` (429) — слишком много попыток
          подтвердить старый пароль.
        * `USER_NOT_FOUND` (404) — JWT валиден, но юзер удалён.

    Audit:
        `user.self_password_reset` (CRITICAL). В `details` — `caller_is_admin`
        (для SIEM-фильтра «admin сменил себе пароль»).
    """
    await user_service.change_own_password(
        db=db,
        user_id=identity.user_id,
        old_password=body.old_password,
        new_password=body.new_password,
        request_id=getattr(request.state, "request_id", None),
    )
    return OkResponse()


# ── Session management (P2: list / revoke-all / revoke-one) ────────────────
# Все три ручки идут под `/users/me/...` и регистрируются ДО любых
# `/users/{user_id}/...`-роутов (см. комментарий выше про `/me/password`):
# FastAPI матчит routes по порядку, иначе "me" улетит в `{user_id}`-параметр.


def _current_session_id(request: Request) -> str | None:
    """Достать `sid` из JWT-payload текущего запроса.

    Middleware `_extract_actor_info` декодит токен и кладёт payload в
    `request.state.jwt_payload`. `sid` появляется только у access-токенов,
    выписанных login'ом/refresh'ем после внедрения фичи; legacy-токены
    дадут None → `revoke_except_current` сделает полный revoke.
    """
    payload = getattr(request.state, "jwt_payload", None)
    if not isinstance(payload, dict):
        return None
    sid = payload.get("sid")
    return sid if isinstance(sid, str) and sid else None


@router.get(
    "/me/sessions",
    response_model=SessionsListResponse,
    summary="Активные сессии текущего пользователя",
    description=(
        "Список активных refresh-сессий юзера (для UI «Active devices»). "
        "Поле `is_current=True` маркирует ту, через `sid` которой пришёл вызов."
    ),
)
async def list_my_sessions(
    request: Request,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> SessionsListResponse:
    """Активные сессии юзера.

    Что возвращает:
        Список `SessionEntry` с `created_at`/`last_used_at`/`expires_at`,
        IP и User-Agent (snapshot момента login'а). Истёкшие или revoked'ы
        не возвращаются.

    Доступ:
        Любой залогиненный юзер (user-context). m2m отбивается
        `require_user_context` ниже по цепочке — у oauth_client нет сессий.

    Audit:
        `user.sessions_listed` (INFO).
    """
    return await user_service.list_sessions(
        db=db,
        user_id=identity.user_id,
        current_session_id=_current_session_id(request),
        request_id=getattr(request.state, "request_id", None),
    )


@router.post(
    "/me/sessions/revoke",
    response_model=RevokeSessionsResponse,
    summary="Logout-all — отозвать все сессии юзера",
    description=(
        "Отзывает все активные refresh-сессии юзера. `except_current=true` "
        "оставляет ту сессию, через `sid` которой пришёл вызов. PAT остаются."
    ),
)
async def revoke_my_sessions(
    body: RevokeSessionsRequest,
    request: Request,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> RevokeSessionsResponse:
    """Logout со всех (или со всех, кроме текущей) устройств.

    Что делает:
        Помечает `is_active=False` + `revoked_at=now` всем активным
        сессиям юзера. PAT и bot-токены НЕ трогаются — отдельная identity.
        Identity-cache сбрасывается, чтобы access-токены с других устройств
        потеряли доступ к /me/introspect мгновенно (а не через TTL).

    `except_current`:
        Если `true` и в JWT есть `sid` — пропускаем эту сессию.
        Если `true`, но `sid` отсутствует (legacy-токен) — делаем полный
        revoke и логируем `current_session_id=None` (UI должен это видеть
        как «пришлось всё снести»).

    Доступ:
        Любой залогиненный юзер (user-context). m2m отбивается.

    Audit:
        `user.sessions_revoked_all` (CRITICAL) с `revoked_count`.
    """
    except_sid: str | None = None
    if body.except_current:
        except_sid = _current_session_id(request)
    revoked = await user_service.revoke_sessions(
        db=db,
        user_id=identity.user_id,
        except_session_id=except_sid,
        request_id=getattr(request.state, "request_id", None),
    )
    return RevokeSessionsResponse(revoked_count=revoked)


@router.delete(
    "/me/sessions/{session_id}",
    response_model=RevokeSessionsResponse,
    summary="Logout одной конкретной сессии",
    description="Целевой revoke одной сессии юзера по session_id.",
    responses={
        404: {"description": "Сессия не найдена, чужая или уже revoked."},
    },
)
async def revoke_one_my_session(
    session_id: str,
    request: Request,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> RevokeSessionsResponse:
    """Revoke одной сессии.

    Что делает:
        Помечает `is_active=False` указанную сессию, если она принадлежит
        текущему юзеру и активна. Иначе 404 `SESSION_NOT_FOUND` — намеренно
        не отличаем «нет» от «чужая», чтобы не было session-id-oracle.

    Доступ:
        Только владелец сессии (user-context).

    Audit:
        `user.session_revoked_one` (WARNING) с `was_current`-флагом.
    """
    revoked = await user_service.revoke_one_session(
        db=db,
        user_id=identity.user_id,
        session_id=session_id,
        current_session_id=_current_session_id(request),
        request_id=getattr(request.state, "request_id", None),
    )
    return RevokeSessionsResponse(revoked_count=revoked)


@router.get(
    "",
    response_model=list[UserResponse],
    summary="Список всех пользователей (глобально)",
    description="Только для account_admin. Видит юзеров всех отделов.",
)
async def list_users(
    request: Request,
    response: Response,
    identity: AccountAdmin,
    pagination: PaginationParams = Depends(pagination_params),
    db: AsyncSession = Depends(get_db),
) -> list[UserResponse]:
    """Глобальный список юзеров.

    Доступ:
        Только account_admin. Department_admin использует
        `/users/department/{department_id}`.

    Пагинация:
        Query-параметры `limit`/`offset`. Полное число записей — в заголовке
        `X-Total-Count`.
    """
    items, total = await user_service.list_users(
        db=db,
        actor_id=identity.user_id,
        pagination=pagination,
        request_id=getattr(request.state, "request_id", None),
    )
    response.headers["X-Total-Count"] = str(total)
    return items


@router.get(
    "/department/{department_id}",
    response_model=list[UserResponse],
    summary="Юзеры конкретного отдела",
    description="Account_admin — любой отдел. Department_admin — только свой.",
)
async def list_users_by_department(
    department_id: str,
    request: Request,
    response: Response,
    identity: AnyAdmin,
    pagination: PaginationParams = Depends(pagination_params),
    db: AsyncSession = Depends(get_db),
) -> list[UserResponse]:
    """Юзеры одного отдела.

    Доступ:
        * account_admin — любой отдел;
        * department_admin — только свой (иначе 404, чтобы не было ID-oracle).

    Пагинация:
        Query-параметры `limit`/`offset`; общее число — в `X-Total-Count`.

    Возможные ошибки:
        * `DEPARTMENT_NOT_FOUND` (404) — нет такого отдела, либо
          department_admin попросил чужой.
    """
    items, total = await user_service.list_users_by_department(
        db=db,
        actor_id=identity.user_id,
        actor_role=identity.platform_role,
        department_id=department_id,
        pagination=pagination,
        request_id=getattr(request.state, "request_id", None),
    )
    response.headers["X-Total-Count"] = str(total)
    return items


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
    description="Перезаписывает пароль (Argon2id хэш) + revoke всех активных сессий и PAT юзера.",
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
        Записывает новый хэш Argon2id (минимум 8 символов, валидируется
        pydantic'ом) и сразу же revoke'ит все активные сессии юзера и все
        его PAT'ы — то есть refresh-токены и долгоживущие токены становятся
        невалидными немедленно.

    Доступ:
        account_admin (любой юзер) или department_admin (только свой отдел).

    Возможные ошибки:
        * `USER_NOT_FOUND` (404).
        * `DEPARTMENT_ISOLATION` (403) — cross-dept у department_admin.
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
        * `BAN_NOT_FOUND` (404) — у юзера нет активного бана.
    """
    await user_service.unban_user(
        db=db,
        actor_id=identity.user_id,
        user_id=user_id,
        request_id=getattr(request.state, "request_id", None),
    )
    return OkResponse()
