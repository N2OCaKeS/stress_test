"""Эндпоинты CRUD пользователей и управления ролями/группами."""

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.constants import UserStatus
from src.core.exceptions import DomainValidationError
from src.dependencies.auth import AccountAdmin, AnyAdmin, CurrentUserIdentity
from src.dependencies.db import get_db
from src.utils.pagination import PaginationParams, pagination_params
from src.schemas.common import OkResponse
from src.schemas.groups import UserGroupsResponse
from src.schemas.users import (
    AddUserToGroupRequest,
    AssignRolesRequest,
    BanRequest,
    HardDeleteUserRequest,
    ResetPasswordRequest,
    RevokeSessionsRequest,
    RevokeSessionsResponse,
    SelfChangePasswordRequest,
    SessionsListResponse,
    UserCreate,
    UserLabelsResponse,
    UserPermissionsResponse,
    UserResolveResponse,
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


# ── Session management (list / revoke-all / revoke-one) ────────────────
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


# ── Admin session management (cross-user) ─────────────────────────────────
# Зеркало /me/sessions/* для админов. account_admin — любой юзер, dep_admin
# — только в своём отделе. Аудит идёт отдельной серией событий
# (`user.sessions_admin_*`) — чтобы SIEM мог отделять admin-инициированные
# revoke'ы от self-инициированных.


@router.get(
    "/{user_id}/sessions",
    response_model=SessionsListResponse,
    summary="Активные сессии юзера (admin view)",
    description=(
        "Список активных refresh-сессий target-юзера. account_admin видит "
        "любого; department_admin — только юзеров своего отдела. "
        "`is_current` всегда False — admin вызывает не из target-сессии."
    ),
    responses={
        403: {"description": "DEPARTMENT_ACCESS_DENIED — DA cross-department."},
        404: {"description": "USER_NOT_FOUND — юзера нет."},
    },
)
async def admin_list_user_sessions(
    user_id: str,
    request: Request,
    identity: AnyAdmin,
    db: AsyncSession = Depends(get_db),
) -> SessionsListResponse:
    """Admin-list сессий чужого юзера.

    Доступ:
        * account_admin — любой;
        * department_admin — только если `target.department_id ==
          actor.department_id` (через `_dept_guard.assert_dept_admin_target_dept`).

    Audit:
        `user.sessions_admin_listed` (INFO).
    """
    return await user_service.admin_list_sessions(
        db=db,
        actor_id=identity.user_id,
        actor_role=identity.platform_role,
        target_user_id=user_id,
        request_id=getattr(request.state, "request_id", None),
    )


@router.post(
    "/{user_id}/sessions/revoke",
    response_model=RevokeSessionsResponse,
    summary="Logout-all для любого юзера (admin)",
    description=(
        "Снести все активные refresh-сессии target-юзера. PAT и bot-токены "
        "не трогаются. account_admin — любой; department_admin — только "
        "в своём отделе."
    ),
    responses={
        403: {"description": "DEPARTMENT_ACCESS_DENIED — DA cross-department."},
        404: {"description": "USER_NOT_FOUND — юзера нет."},
    },
)
async def admin_revoke_user_sessions(
    user_id: str,
    request: Request,
    identity: AnyAdmin,
    db: AsyncSession = Depends(get_db),
) -> RevokeSessionsResponse:
    """Admin-revoke всех сессий чужого юзера.

    Доступ:
        account_admin (cross-dept) или department_admin (свой отдел).

    Body отсутствует: `except_current` для admin-revoke бессмысленен —
    у actor'а своя сессия, не target'а.

    Audit:
        `user.sessions_admin_revoked_all` (CRITICAL).
    """
    revoked = await user_service.admin_revoke_all_sessions(
        db=db,
        actor_id=identity.user_id,
        actor_role=identity.platform_role,
        target_user_id=user_id,
        request_id=getattr(request.state, "request_id", None),
    )
    return RevokeSessionsResponse(revoked_count=revoked)


@router.delete(
    "/{user_id}/sessions/{session_id}",
    response_model=RevokeSessionsResponse,
    summary="Revoke одной сессии чужого юзера (admin)",
    description="Целевой revoke одной сессии. session_id должна принадлежать user_id, иначе 404.",
    responses={
        403: {"description": "DEPARTMENT_ACCESS_DENIED — DA cross-department."},
        404: {"description": "USER_NOT_FOUND или SESSION_NOT_FOUND."},
    },
)
async def admin_revoke_user_session_by_id(
    user_id: str,
    session_id: str,
    request: Request,
    identity: AnyAdmin,
    db: AsyncSession = Depends(get_db),
) -> RevokeSessionsResponse:
    """Admin-revoke одной сессии.

    Доступ: те же правила, что и list/revoke-all. 404
    `SESSION_NOT_FOUND` — сессия не принадлежит указанному `user_id` или
    уже revoked (намеренно не различаем, чтобы не было session-id oracle).

    Audit: `user.session_admin_revoked_one` (WARNING).
    """
    revoked = await user_service.admin_revoke_one_session(
        db=db,
        actor_id=identity.user_id,
        actor_role=identity.platform_role,
        target_user_id=user_id,
        session_id=session_id,
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
    include_banned: bool = Query(
        False,
        description=(
            "Снять фильтр `is_active`: вернуть и забаненных/заблокированных. "
            "Без флага — только active (поведение UI по умолчанию)."
        ),
    ),
    status: str | None = Query(
        None,
        description=(
            "Пост-фильтр по статусу: `active` / `banned` / `blocked`. "
            "Если задан — `include_banned` неявно True."
        ),
    ),
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
    if status is not None:
        try:
            UserStatus(status)
        except ValueError:
            raise DomainValidationError(
                error_code="INVALID_STATUS_FILTER",
                message=f"invalid status filter: {status!r}",
            )
    items, total = await user_service.list_users(
        db=db,
        actor_id=identity.user_id,
        pagination=pagination,
        request_id=getattr(request.state, "request_id", None),
        include_banned=include_banned,
        status_filter=status,
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
    include_banned: bool = Query(
        False,
        description=(
            "Снять фильтр `is_active`: вернуть забаненных/заблокированных тоже."
        ),
    ),
    status: str | None = Query(
        None,
        description=(
            "Пост-фильтр по статусу: `active` / `banned` / `blocked`. "
            "Если задан — `include_banned` неявно True."
        ),
    ),
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
    if status is not None:
        try:
            UserStatus(status)
        except ValueError:
            raise DomainValidationError(
                error_code="INVALID_STATUS_FILTER",
                message=f"invalid status filter: {status!r}",
            )
    items, total = await user_service.list_users_by_department(
        db=db,
        actor_id=identity.user_id,
        actor_role=identity.platform_role,
        department_id=department_id,
        pagination=pagination,
        request_id=getattr(request.state, "request_id", None),
        include_banned=include_banned,
        status_filter=status,
    )
    response.headers["X-Total-Count"] = str(total)
    return items


# `/users/resolve` регистрируется ДО `/{user_id}`-роутов — иначе FastAPI
# съел бы `resolve` как литеральный user_id и вернул 404 USER_NOT_FOUND.
@router.get(
    "/resolve",
    response_model=UserResolveResponse,
    summary="Резолв username → user_id (точечный, dept-scoped)",
    description=(
        "Точное совпадение username → `{user_id, username, department_id}`. "
        "Для адресации шаринга personal-секретов конкретному человеку, когда "
        "списки юзеров недоступны. Обычный юзер и department_admin видят только "
        "свой отдел; чужой/несуществующий username → 404 (без enumeration). "
        "account_admin резолвит cross-dept."
    ),
    responses={
        404: {"description": "USER_NOT_FOUND — нет такого username в видимом scope."},
    },
)
async def resolve_user(
    request: Request,
    identity: CurrentUserIdentity,
    username: str = Query(
        min_length=1,
        max_length=128,
        description="Точный username для резолва (без fuzzy-перечисления).",
    ),
    db: AsyncSession = Depends(get_db),
) -> UserResolveResponse:
    """Точечный username → user_id lookup.

    Доступ:
        Любой залогиненный юзер (user-context). m2m отбивается
        `require_user_context` (403 USER_CONTEXT_REQUIRED). Видимость scope —
        свой отдел (account_admin — любой). Чувствительные поля не отдаются.

    Возможные ошибки:
        * `USER_NOT_FOUND` (404) — нет такого username в видимом scope.
    """
    return await user_service.resolve_username(
        db=db,
        identity=identity,
        username=username,
        request_id=getattr(request.state, "request_id", None),
    )


# `/users/labels` — как и `/resolve`, регистрируется ДО `/{user_id}`, иначе
# FastAPI съел бы `labels` как литеральный user_id.
@router.get(
    "/labels",
    response_model=UserLabelsResponse,
    summary="Батч-резолв user_id → username (любой залогиненный юзер)",
    description=(
        "Принимает `ids` (CSV из user_id) и возвращает `{user_id: username}` "
        "только для найденных. Username — не чувствительные данные, поэтому "
        "доступен любому user-context (не только админам): UI подставляет имя "
        "вместо id, например в карточке шаринга personal-секрета. "
        "Несуществующие id молча пропускаются. Лимит — 200 id за запрос."
    ),
)
async def resolve_user_labels(
    identity: CurrentUserIdentity,
    ids: str = Query(
        min_length=1,
        max_length=8192,
        description="Список user_id через запятую (например `usr_a,usr_b`).",
    ),
    db: AsyncSession = Depends(get_db),
) -> UserLabelsResponse:
    """Батч user_id → username.

    Доступ:
        Любой залогиненный юзер (user-context). m2m отбивается
        `require_user_context` (403 USER_CONTEXT_REQUIRED). Чувствительные поля
        не отдаются — только id→username.
    """
    user_ids = [i.strip() for i in ids.split(",") if i.strip()]
    # Защита от слишком большого батча — режем до разумного потолка.
    user_ids = user_ids[:200]
    return await user_service.resolve_labels(db=db, user_ids=user_ids)


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
        * `USER_ALREADY_EXISTS` (409) — username уже занят.
        * `DEPARTMENT_NOT_FOUND` (404) — указанный department_id не найден.
        * `DEPARTMENT_ACCESS_DENIED` (403) — department_admin пытается создать
          в чужом отделе.
        * `PLATFORM_ROLE_ASSIGNMENT_DENIED` (403) — не-account_admin пытается
          выдать `platform_role`.
        * `CANNOT_BYPASS_PASSWORD_CHANGE` (403) — не-account_admin прислал
          `must_change_password=false` (force-change может снять только account_admin).
        * `MISSING_REQUIRED_FIELD` (400) — `department_id` опущен для обычного
          юзера (не платформенного админа).
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
        must_change_password=body.must_change_password,
        request_id=getattr(request.state, "request_id", None),
    )


@router.get(
    "/{user_id}",
    response_model=UserResponse,
    summary="Получить юзера по id",
    description="Single-user read для admin UI. List+filter на клиенте — overkill, см. внизу.",
)
async def get_user(
    user_id: str,
    request: Request,
    identity: AnyAdmin,
    db: AsyncSession = Depends(get_db),
) -> UserResponse:
    """Получить юзера по user_id.

    Что делает:
        Возвращает того же `UserResponse` shape, что и `GET /users`
        (с именем отдела). Для UI-карточки юзера в админке —
        чтобы не таскать весь list+filter ради одного row'а.

    Доступ:
        Любой админ (`AnyAdmin`). department_admin'у НЕ ограничивается
        scope на этом endpoint'е: список через `GET /users` он и так не
        видит вне своего отдела, а карточка по id — read-only детали и
        не открывает векторов привилегий.

    Возможные ошибки:
        * `USER_NOT_FOUND` (404).
    """
    return await user_service.get_user(
        db=db,
        user_id=user_id,
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
        # exclude_unset (а не exclude_none) — иначе фронтенд не может
        # очистить nullable-поля (department_id / platform_role / email):
        # отсутствующий ключ и явный null приходили бы одинаково и оба
        # терялись на стадии «drop None». exclude_unset оставляет именно
        # те ключи, которые клиент написал в JSON, включая null'ы.
        updates=body.model_dump(exclude_unset=True),
        request_id=getattr(request.state, "request_id", None),
    )


@router.post(
    "/{user_id}/roles",
    response_model=OkResponse,
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
        * `USER_NOT_FOUND` (404) / `USER_INACTIVE` (409).
        * `USER_ROLE_UPDATE_FORBIDDEN` (403) — DA назначает роли юзеру чужого отдела.
        * `SERVICE_NOT_ALLOWED_FOR_DEPARTMENT` (403) — отдел не имеет access к сервису.
        * `INVALID_SERVICE_ROLE` (422) — роль не определена в `ServiceRoleDefinition`
          для пары `(department, service)`.
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
        * `USER_RESET_PASSWORD_FORBIDDEN` (403) — DA сбрасывает пароль юзеру
          чужого отдела.
        * `ACTOR_VANISHED` (401) — actor-юзер удалён между JWT-выдачей и вызовом.
    """
    await user_service.reset_password(
        db=db,
        actor_id=identity.user_id,
        user_id=user_id,
        new_password=body.new_password,
        actor_role=identity.platform_role,
        request_id=getattr(request.state, "request_id", None),
    )
    return OkResponse()


@router.post(
    "/{user_id}/force-password-change",
    response_model=OkResponse,
    summary="Форсировать смену пароля юзеру на ближайшем входе",
    description=(
        "Поднимает `must_change_password=True` без замены пароля. "
        "После этого middleware пускает target'а только на `/users/me/password` "
        "до тех пор, пока он не сменит пароль через self-service. "
        "Активные сессии и PAT не revoke'ятся — guard отрежет их на следующем "
        "запросе по флагу в БД."
    ),
    responses={
        403: {"description": "ROLE_REQUIRED / DEPT_MISMATCH — actor не admin или DA вне отдела target'а."},
        404: {"description": "USER_NOT_FOUND."},
    },
)
async def force_password_change_user(
    user_id: str,
    request: Request,
    identity: AnyAdmin,
    db: AsyncSession = Depends(get_db),
) -> OkResponse:
    """Standalone flag-flip `must_change_password=True`.

    Что делает:
        Ставит `target.must_change_password=True`, сбрасывает identity-cache
        target'а (иначе guard на других ручках не сработает до TTL'а),
        пишет audit `user.force_password_change` (WARNING). Самому себе
        ставить флаг разрешено — полезно, чтобы admin мог проверить flow
        на своём аккаунте.

    Доступ:
        * account_admin — любой target;
        * department_admin — только юзер своего отдела (иначе 403
          DEPT_MISMATCH);
        * иначе — 403 ROLE_REQUIRED (отбивается `AnyAdmin`-guard'ом).

    Возможные ошибки:
        * `USER_NOT_FOUND` (404).
        * `DEPT_MISMATCH` (403) — DA пытается дёрнуть юзера чужого отдела.
        * `ACTOR_VANISHED` (403) — actor удалён между issue JWT и check'ом.
    """
    await user_service.force_password_change(
        db=db,
        actor_id=identity.user_id,
        actor_role=identity.platform_role,
        actor_dept_id=identity.department_id,
        target_user_id=user_id,
        request_id=getattr(request.state, "request_id", None),
    )
    return OkResponse()


@router.post(
    "/{user_id}/ban",
    response_model=OkResponse,
    summary="Забанить юзера",
    description="Permanent или temporary. Убивает все сессии + PAT юзера. Боты отдела (даже созданные им) намеренно не трогаются.",
)
async def ban_user(
    user_id: str,
    body: BanRequest,
    request: Request,
    identity: AnyAdmin,
    db: AsyncSession = Depends(get_db),
) -> OkResponse:
    """Забанить юзера.

    Что делает:
        Создаёт `Ban` (permanent/temporary), revoke'ит все активные сессии
        юзера + все его PAT. Боты, которыми он владеет, и их токены остаются
        живыми (бот — отдельная identity отдела). `temporary` обязательно
        требует `expires_at` в будущем.

    Доступ:
        * account_admin — любой target;
        * department_admin — только юзер своего отдела (иначе 403
          DEPT_MISMATCH); платформенного юзера DA забанить не может.

    Возможные ошибки:
        * `USER_NOT_FOUND` (404).
        * `CANNOT_BAN_SELF` (422) — забанить себя нельзя.
        * `DEPT_MISMATCH` (403) — DA по чужому/платформенному юзеру.
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
        actor_role=identity.platform_role,
        actor_dept_id=identity.department_id,
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
        * `GROUP_DEPARTMENT_MISMATCH` (403) — юзер и группа в разных отделах.
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


@router.delete(
    "/{user_id}",
    response_model=OkResponse,
    summary="Hard-delete юзера (с указанием причины)",
    description=(
        "Жёсткое удаление юзера: row в `users` сносится физически. "
        "ORM-cascade уносит сессии/PAT/Ban/UserServiceRole/UserGroupMembership. "
        "Ботов юзера НЕ трогаем — бот это dept-owned entity. "
        "После commit'а secret_service получает best-effort notify "
        "(`/internal/lifecycle/user-deleted`), который блокирует personal "
        "credentials удалённого юзера."
    ),
    responses={
        404: {"description": "USER_NOT_FOUND — юзера нет."},
        422: {"description": "LAST_ACCOUNT_ADMIN — нельзя удалить последнего активного account_admin'а."},
        403: {"description": "PERMISSION_DENIED / ROLE_REQUIRED — нужен account_admin."},
    },
)
async def hard_delete_user(
    user_id: str,
    body: HardDeleteUserRequest,
    request: Request,
    identity: AccountAdmin,
    db: AsyncSession = Depends(get_db),
) -> OkResponse:
    """Hard-delete юзера.

    Доступ:
        Только `account_admin` (платформенный, без отдела). Department_admin
        не пускаем намеренно — hard-delete сразу касается секретов в
        secret_service и должен идти через одного и того же тип actor'а,
        что и create/transfer.

    Защита:
        Запрет удалять последнего активного account_admin'а
        (`LAST_ACCOUNT_ADMIN` 422), иначе платформа теряет admin-управление.

    Возможные ошибки:
        * `USER_NOT_FOUND` (404).
        * `LAST_ACCOUNT_ADMIN` (422) — единственный оставшийся
          account_admin защищён от снесения.
        * `ROLE_REQUIRED` (403) — actor не account_admin.

    Audit:
        `user.hard_deleted` (CRITICAL). `details.reason` — то, что
        прислал юзер. Также пишутся `sessions_revoked` и `pat_revoked_count`.
    """
    await user_service.hard_delete_user(
        db=db,
        actor_id=identity.user_id,
        actor_username=identity.username,
        user_id=user_id,
        reason=body.reason,
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
    identity: AnyAdmin,
    db: AsyncSession = Depends(get_db),
) -> OkResponse:
    """Снять бан.

    Доступ:
        * account_admin — любой target;
        * department_admin — только юзер своего отдела (иначе 403
          DEPT_MISMATCH). Симметрично ban-у.

    Возможные ошибки:
        * `USER_NOT_FOUND` (404).
        * `DEPT_MISMATCH` (403) — DA по чужому/платформенному юзеру.
        * `BAN_NOT_FOUND` (404) — у юзера нет активного бана.
    """
    await user_service.unban_user(
        db=db,
        actor_id=identity.user_id,
        user_id=user_id,
        request_id=getattr(request.state, "request_id", None),
        actor_role=identity.platform_role,
        actor_dept_id=identity.department_id,
    )
    return OkResponse()


@router.post(
    "/{user_id}/unlock",
    response_model=OkResponse,
    summary="Снять brute-force lockout с юзера",
    description="Сбрасывает счётчик неудачных логинов и lockout-окно. Idempotent: разлочить незалоченного — no-op.",
)
async def unlock_user(
    user_id: str,
    request: Request,
    identity: AnyAdmin,
    db: AsyncSession = Depends(get_db),
) -> OkResponse:
    """Снять lockout по неудачным логинам.

    Что делает:
        Обнуляет `failed_login_attempts` и `locked_until` — после этого юзер
        снова может логиниться, не дожидаясь истечения lockout-окна. Бан и
        статус не затрагиваются (для них есть `/ban` и `/unban`).

    Доступ:
        * account_admin — любой target;
        * department_admin — только юзер своего отдела (иначе 403
          DEPT_MISMATCH). Симметрично ban/unban.

    Возможные ошибки:
        * `USER_NOT_FOUND` (404).
        * `DEPT_MISMATCH` (403) — DA по чужому/платформенному юзеру.
    """
    await user_service.unlock_user(
        db=db,
        actor_id=identity.user_id,
        user_id=user_id,
        request_id=getattr(request.state, "request_id", None),
        actor_role=identity.platform_role,
        actor_dept_id=identity.department_id,
    )
    return OkResponse()
