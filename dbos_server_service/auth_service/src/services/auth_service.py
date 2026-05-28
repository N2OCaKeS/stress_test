"""Workflow аутентификации и сессий: login / refresh / logout / get_identity."""

from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import get_settings
from src.core.constants import PlatformRole, UserStatus
from src.core.exceptions import AuthenticationError, AuthorizationError
from src.core.security import (
    create_access_token,
    generate_refresh_token,
    hash_refresh_token,
    verify_password,
)
from src.repositories.departments import DepartmentRepository
from src.repositories.groups import GroupRepository
from src.repositories.roles import RoleRepository
from src.repositories.sessions import SessionRepository
from src.repositories.users import UserRepository
from src.schemas.auth import IdentityContext, LoginResponse, RefreshResponse
from src.services import _lockout, audit_context, audit_service
from src.utils.time import expires_at, is_expired

# Dummy Argon2id hash для timing-equalisation при login несуществующего
# юзера. Argon2id verify ~100ms; без этого вызова unknown-user отбивается
# мгновенно, known-user уходит на полный Argon2 → username enumeration по
# таймингу. Хэш генерится один раз при импорте под фиксированным секретом
# (значение не важно, важна валидная argon2id-форма с нашими параметрами,
# чтобы verify сделал реальную работу, а не упал на InvalidHashError).
from src.core.security import hash_password as _hash_password
_DUMMY_PASSWORD_HASH = _hash_password("__never_match_sentinel__")


async def verify_password_with_lockout(
    db: AsyncSession,
    user,
    password: str,
) -> bool:
    """Общий helper verify_password + lockout, используется `/login` и `/docker/token`.

    Поведение (зеркалит `/login`-flow):

    1. Если `locked_until` есть и уже истёк — сбрасываем `failed_login_attempts`
       в ноль атомарно и коммитим (идемпотентно при concurrent-вызовах).
    2. Если `locked_until` активен — `AuthorizationError`
       `ACCOUNT_TEMPORARILY_LOCKED` (HTTP 429) с `retry_after_seconds`.
    3. Запускаем `verify_password` (Argon2id, ~100 мс). При неудаче:

       * атомарно `UPDATE users SET failed_login_attempts = failed_login_attempts + 1 RETURNING …`;
       * если новое значение перевалило `max_failed_login_attempts` — ставим
         `locked_until = now + lockout_minutes`;
       * commit ДО raise, иначе `get_db()`-rollback стирает счётчик (уже
         наступали на эти грабли в `/login`).

    Возвращает `True` при успехе. При неудаче:

      * `INVALID_CREDENTIALS` — неверный пароль.
      * `ACCOUNT_TEMPORARILY_LOCKED` — аккаунт залочен (пароль **не** проверяем,
        чтобы не палить timing на brute-forcer'а и не платить Argon2id).

    Helper НЕ чекает `user.status` (это делают caller'ы — `/login` отбивает
    BANNED через `USER_BANNED`, `/docker/token` через `INVALID_CREDENTIALS`,
    чтобы скрыть существование юзера). Audit-события тоже не эмитим — caller
    эмитит свои (`user.login` vs `docker.token_issued`).
    """
    user_repo = UserRepository(db)
    settings = get_settings()

    if await _lockout.release_if_expired(user_repo, user):
        await db.commit()

    _lockout.assert_not_locked(user)

    if not verify_password(password, user.password_hash):
        # Commit ДО raise — иначе внешний get_db() роллбэкает инкремент
        # и brute-force protection тихо ломается.
        await _lockout.register_failure(
            user_repo,
            user,
            max_attempts=settings.max_failed_login_attempts,
            lockout_minutes=settings.lockout_minutes,
        )
        await db.commit()
        raise AuthenticationError(
            error_code="INVALID_CREDENTIALS",
            message="Invalid username or password",
        )

    await _lockout.register_success(user_repo, user)
    return True


def _merge_permissions(
    dept_services: list[str],
    direct_roles: dict[str, list[str]],
    group_services: list[str],
    group_roles: dict[str, list[str]],
) -> tuple[list[str], dict[str, list[str]]]:
    """Смержить dept/group service-access и direct/group роли в effective view.

    **Cross-dept privilege retention guard.** Effective роли пересекаем
    INTERSECT'ом с `allowed_services` (= `dept_services ∪ group_services`).
    У `user_service_roles` нет колонки `department_id`, поэтому строки
    переживают перевод юзера между отделами. Без фильтра юзер, бывший
    admin'ом config_service в отделе A, после перевода в B (без access
    к config_service) всё ещё бы отдавал `{config_service: [admin]}` —
    `direct_roles` грузится из `user_service_roles` только по `user_id`.

    Инвариант: у юзера не может быть effective `service_role` для сервиса,
    к которому текущий отдел (или его группы) не имеют активного access.
    `DepartmentServiceAccess` остаётся единственным источником истины по
    service-visibility; `user_service_roles` — attribute layer сверху, не override.

    Group-роли (`group_service_roles`) текут точно так же если group_service
    был revoked, а role-row остался — фильтруем тем же INTERSECT для симметрии.
    """
    services = list(set(dept_services + group_services))
    services_set = set(services)
    merged: dict[str, list[str]] = {}
    for svc, roles in direct_roles.items():
        if svc not in services_set:
            # SECURITY: stale role-строка указывает на сервис, к которому юзер
            # потерял access (cross-dept transfer / dept→service revoke /
            # group leave) — выкидываем. Подробнее — в docstring.
            continue
        merged.setdefault(svc, []).extend(roles)
    for svc, roles in group_roles.items():
        if svc not in services_set:
            continue
        merged.setdefault(svc, []).extend(roles)
    return services, {svc: list(set(roles)) for svc, roles in merged.items()}


async def collect_user_permissions(
    db: AsyncSession, user, oauth_scopes: list[str] | None = None
) -> tuple[list[str], dict[str, list[str]]]:
    """Пересчитать effective (allowed_services, service_roles) юзера из БД.

    Single source of truth для login/refresh/get_identity И для JWT introspect
    revalidate'а (чтобы протухший JWT не держал отозванную роль/сервис до
    конца TTL).

    `oauth_scopes` (опционально): если JWT выписан через OAuth2
    `authorization_code` grant, в payload лежит снапшот `oauth_scopes`
    (фактически выданные клиенту `auth_code.scopes`). При revalidate надо
    пересечь live-права юзера с этим набором — иначе third-party приложение,
    получившее токен с ограниченным scope, увидит через introspect полные
    права юзера (scope-creep). Для не-OAuth токенов параметр == None и
    фильтрация не применяется.
    """
    role_repo = RoleRepository(db)
    dept_repo = DepartmentRepository(db)
    group_repo = GroupRepository(db)

    dept_services = (
        await dept_repo.list_active_services(user.department_id)
        if user.department_id
        else []
    )
    direct_roles = await role_repo.get_all_roles(user.id)
    group_services = await group_repo.list_active_services_for_user(user.id)
    group_roles = await group_repo.get_roles_for_user(user.id)
    allowed_services, service_roles = _merge_permissions(
        dept_services, direct_roles, group_services, group_roles
    )

    if oauth_scopes is not None:
        # OAuth authorization_code: пересекаем live-права с зафиксированным
        # в коде scope. Пустой список scopes → пустые права (а не "any").
        # TODO: подумать, есть ли смысл также интерсектить с allowed_scopes клиента
        scope_set = set(oauth_scopes)
        allowed_services = [s for s in allowed_services if s in scope_set]
        service_roles = {s: r for s, r in service_roles.items() if s in scope_set}

    return allowed_services, service_roles


async def collect_bot_permissions(
    db: AsyncSession, bot
) -> tuple[list[str], dict[str, list[str]]]:
    """Пересчитать effective (allowed_services, service_roles) бота из БД.

    Аналог `collect_user_permissions` для service-account'ов. Забор сервисов —
    `bot.allowed_services`, пересечённый с активными сервисами отдела
    (`DepartmentServiceAccess`): группа даёт боту РОЛИ, но не расширяет список
    сервисов. Эффективные роли = прямые `bot_service_roles` ∪ роли из групп
    бота, отфильтрованные тем же INTERSECT'ом по `effective_services` (stale
    direct/group роль для сервиса вне забора отбрасывается).
    """
    from src.repositories.bot_roles import BotRoleRepository

    dept_repo = DepartmentRepository(db)
    group_repo = GroupRepository(db)
    bot_role_repo = BotRoleRepository(db)

    dept_services = set(await dept_repo.list_active_services(bot.department_id))
    effective_services = [s for s in (bot.allowed_services or []) if s in dept_services]
    effective_set = set(effective_services)

    direct_roles = await bot_role_repo.get_all_roles(bot.id)
    group_roles = await group_repo.get_roles_for_bot(bot.id)

    merged: dict[str, list[str]] = {}
    for svc, roles in direct_roles.items():
        if svc not in effective_set:
            continue
        merged.setdefault(svc, []).extend(roles)
    for svc, roles in group_roles.items():
        if svc not in effective_set:
            continue
        merged.setdefault(svc, []).extend(roles)
    service_roles = {svc: list(set(roles)) for svc, roles in merged.items()}
    return effective_services, service_roles


def _build_identity(user, dept_name: str | None, allowed_services: list[str], service_roles: dict) -> IdentityContext:
    """Собрать `IdentityContext` для login/refresh/get_identity-ответов.

    Для account_admin зануляем allowed_services/service_roles — у них нет
    отдела, и сами по себе они не являются service-юзерами (admin-роли на
    platform-уровне).
    """
    is_account_admin = user.platform_role == PlatformRole.ACCOUNT_ADMIN
    return IdentityContext(
        user_id=user.id,
        username=user.username,
        department_id=user.department_id,
        department_name=dept_name,
        allowed_services=[] if is_account_admin else allowed_services,
        service_roles={} if is_account_admin else service_roles,
        is_banned=user.status == UserStatus.BANNED,
        platform_role=user.platform_role,
    )


def _build_access_token(user) -> str:
    """Сминтить access JWT для юзера. TTL — из settings.

    В payload кладём минимум: `sub` + `actor_type` (+ iat/exp/iss/aud от
    `create_access_token`). Никаких чувствительных claims —
    `allowed_services`/`service_roles`/`platform_role`/`department_id`/
    `username` декодируются из base64 без ключа и палят права/принадлежность
    отделу через любой логированный или утёкший токен. Все эти поля живые
    данные: `get_current_identity` и `introspect` revalidate'ят их из БД на
    каждом запросе, в payload они не нужны.
    """
    settings = get_settings()
    return create_access_token(
        payload={
            "sub": user.id,
            # `actor_type` нужен `authorization_service.introspect` и
            # `get_current_identity` для диспатча revalidate-пути:
            # "user" → UserRepository, "oauth_client" → OAuthClientRepository.
            "actor_type": "user",
        },
        expires_delta=timedelta(minutes=settings.access_token_ttl_minutes),
    )


async def login(
    db: AsyncSession,
    username: str,
    password: str,
    ip_address: str | None = None,
    user_agent: str | None = None,
    request_id: str | None = None,
) -> LoginResponse:
    """Логин юзера: verify password → создать сессию → выдать access + refresh.

    Lockout: 5 неудач подряд → 15 мин лок. Pipeline идентичен `/docker/token`
    через `verify_password_with_lockout`.
    """
    settings = get_settings()
    user_repo = UserRepository(db)
    session_repo = SessionRepository(db)
    role_repo = RoleRepository(db)
    dept_repo = DepartmentRepository(db)
    group_repo = GroupRepository(db)

    user = await user_repo.get_by_username(username)
    if user is None:
        # Жжём Argon2id на dummy-хэше — timing симметричен случаю «юзер есть,
        # пароль неверный». Иначе по latency можно было перебирать username'ы.
        verify_password(password, _DUMMY_PASSWORD_HASH)
        audit_service.emit("user.login", None, status="failure", allowed=False, details={"username": username, "reason": "user_not_found"}, request_id=request_id)
        raise AuthenticationError(error_code="INVALID_CREDENTIALS", message="Invalid username or password")

    # ── Just-in-time auto-unban для истёкшего temporary ban'а ─────────────────
    # Если у юзера активный `Ban` с `expires_at < now` — снимаем inline, чтобы
    # 24h-ban не превратился в eternal без явного unban. См.
    # `user_service.auto_unban_if_expired` — он deactivate'ит ban-row, синкает
    # user.status/is_active, коммитит и эмитит audit `user.unban`
    # (`source="auto"`, `actor_id=None`). После успеха продолжаем обычный login.
    if user.status == UserStatus.BANNED:
        from src.services import user_service
        if await user_service.auto_unban_if_expired(db, user, request_id=request_id):
            # Ban deactivated, user.status/is_active уже обновлены в auto_unban;
            # перечитываем ORM-инстанс на случай stale state.
            await db.refresh(user)
        else:
            audit_service.emit("user.login", user.id, status="failure", allowed=False, details={"reason": "banned", "username": user.username}, request_id=request_id)
            raise AuthorizationError(error_code="USER_BANNED", message="User is banned")

    if user.status == UserStatus.BLOCKED:
        audit_service.emit("user.login", user.id, status="failure", allowed=False, details={"reason": "blocked", "username": user.username}, request_id=request_id)
        raise AuthorizationError(error_code="USER_BLOCKED", message="User is blocked")

    # Общий lockout + verify_password pipeline. Кидает AuthorizationError
    # (ACCOUNT_TEMPORARILY_LOCKED, 429) или AuthenticationError
    # (INVALID_CREDENTIALS, 401). При успехе — счётчик сброшен, идём дальше
    # создавать сессию.
    try:
        await verify_password_with_lockout(db, user, password)
    except AuthorizationError as exc:
        # Re-emit audit с `/login`-специфичной рамкой перед прокидыванием.
        if exc.error_code == "ACCOUNT_TEMPORARILY_LOCKED":
            audit_service.emit(
                "user.login", user.id, status="failure", allowed=False,
                details={
                    "reason": "account_locked",
                    "username": user.username,
                    "retry_after_seconds": exc.details.get("retry_after_seconds"),
                },
                request_id=request_id,
            )
        raise
    except AuthenticationError:
        audit_service.emit(
            "user.login", user.id, status="failure", allowed=False,
            details={
                "reason": "invalid_password",
                "username": user.username,
                "attempts": user.failed_login_attempts,
                "max_attempts": get_settings().max_failed_login_attempts,
            },
            request_id=request_id,
        )
        raise

    dept_services = await dept_repo.list_active_services(user.department_id) if user.department_id else []
    direct_roles = await role_repo.get_all_roles(user.id)
    group_services = await group_repo.list_active_services_for_user(user.id)
    group_roles = await group_repo.get_roles_for_user(user.id)
    allowed_services, service_roles = _merge_permissions(dept_services, direct_roles, group_services, group_roles)
    dept = await dept_repo.get_by_id(user.department_id) if user.department_id else None

    raw_refresh, refresh_hash = generate_refresh_token()
    refresh_expires = expires_at(days=settings.refresh_token_ttl_days)
    await session_repo.create(
        user_id=user.id,
        refresh_token_hash=refresh_hash,
        expires_at=refresh_expires,
        ip_address=ip_address,
        user_agent=user_agent,
    )

    access_token = _build_access_token(user)
    await db.commit()

    audit_context.update_context(
        actor_id=user.id, username=user.username, department_id=user.department_id,
    )
    audit_service.emit(
        "user.login", user.id, status="success", request_id=request_id,
        details={
            "username": username,
            "department_id": user.department_id,
            "platform_role": user.platform_role,
            "allowed_services_count": len(allowed_services),
            "service_roles_summary": {k: list(v) for k, v in service_roles.items()},
        },
    )
    return LoginResponse(
        access_token=access_token,
        refresh_token=raw_refresh,
        expires_in=settings.access_token_ttl_minutes * 60,
        identity=_build_identity(user, dept.display_name if dept else None, allowed_services, service_roles),
    )


async def refresh(db: AsyncSession, raw_refresh_token: str, request_id: str | None = None) -> RefreshResponse:
    """Ротация refresh: старый → новая пара. Reuse-detection через `previous_token_hash`."""
    settings = get_settings()
    session_repo = SessionRepository(db)
    user_repo = UserRepository(db)

    token_hash = hash_refresh_token(raw_refresh_token)
    sess = await session_repo.get_active_by_token_hash(token_hash)

    if sess is None:
        old_sess = await session_repo.get_by_token_hash(token_hash)
        if old_sess:
            await session_repo.mark_suspicious(old_sess)
            await session_repo.revoke_all_for_user(old_sess.user_id)
            await db.commit()
            audit_service.emit("token.refresh_reuse", old_sess.user_id, status="failure", allowed=False, details={"session_id": old_sess.id}, request_id=request_id)
        raise AuthenticationError(error_code="REFRESH_TOKEN_INVALID", message="Invalid refresh token")

    if is_expired(sess.expires_at):
        await session_repo.revoke(sess)
        await db.commit()
        raise AuthenticationError(error_code="REFRESH_TOKEN_EXPIRED", message="Refresh token expired")

    user = await user_repo.get_by_id(sess.user_id)
    if user is None:
        await session_repo.revoke(sess)
        await db.commit()
        raise AuthorizationError(error_code="USER_NOT_FOUND", message="User not found")
    if user.status == UserStatus.BANNED:
        await session_repo.revoke(sess)
        await db.commit()
        raise AuthorizationError(error_code="USER_BANNED", message="User is banned")
    if user.status == UserStatus.BLOCKED:
        await session_repo.revoke(sess)
        await db.commit()
        raise AuthorizationError(error_code="USER_BLOCKED", message="User is blocked")

    new_raw, new_hash = generate_refresh_token()
    new_expires = expires_at(days=settings.refresh_token_ttl_days)
    rotated = await session_repo.rotate(sess, new_hash, new_expires)
    if not rotated:
        # CAS-miss: другой concurrent /refresh уже ротировал эту сессию (оба
        # запроса прочитали одну строку до того, как кто-то её UPDATE'нул).
        # Это benign-race, НЕ token reuse — НЕ зовём `mark_suspicious` и
        # `revoke_all_for_user`. Легитимный победитель уже получил свежие
        # токены, этот caller просто ретраит с новым RT. Откатываем pending
        # writes (в нормальном flow их нет, но держим сессию чистой для audit).
        await db.rollback()
        audit_service.emit(
            "token.refresh_race",
            user.id,
            status="failure",
            allowed=False,
            details={"session_id": sess.id, "reason": "concurrent_rotation"},
            request_id=request_id,
        )
        raise AuthenticationError(
            error_code="REFRESH_TOKEN_RACE",
            message="Refresh token was rotated by a concurrent request; retry with the new token.",
        )

    access_token = _build_access_token(user)
    await db.commit()

    audit_context.update_context(
        actor_id=user.id, username=user.username, department_id=user.department_id,
    )
    audit_service.emit(
        "user.refresh", user.id, status="success", request_id=request_id,
        details={
            "session_id": sess.id,
            "username": user.username,
            "department_id": user.department_id,
        },
    )
    return RefreshResponse(
        access_token=access_token,
        refresh_token=new_raw,
        expires_in=settings.access_token_ttl_minutes * 60,
    )


async def logout(db: AsyncSession, raw_refresh_token: str, request_id: str | None = None) -> None:
    """Logout — пометить refresh revoked. Несуществующий токен тихо ok."""
    session_repo = SessionRepository(db)
    token_hash = hash_refresh_token(raw_refresh_token)
    sess = await session_repo.get_active_by_token_hash(token_hash)
    if sess:
        user_id = sess.user_id
        await session_repo.revoke(sess)
        await db.commit()
        audit_service.emit(
            "user.logout", user_id, status="success", request_id=request_id,
            details={"session_id": sess.id},
        )


async def get_identity(db: AsyncSession, user_id: str, request_id: str | None = None) -> IdentityContext:
    """Свежий identity-снимок юзера из БД. Используется `/me`."""
    user_repo = UserRepository(db)
    role_repo = RoleRepository(db)
    dept_repo = DepartmentRepository(db)
    group_repo = GroupRepository(db)

    user = await user_repo.get_by_id(user_id)
    if user is None:
        raise AuthenticationError(error_code="USER_NOT_FOUND", message="User not found")

    dept_services = await dept_repo.list_active_services(user.department_id) if user.department_id else []
    direct_roles = await role_repo.get_all_roles(user.id)
    group_services = await group_repo.list_active_services_for_user(user.id)
    group_roles = await group_repo.get_roles_for_user(user.id)
    allowed_services, service_roles = _merge_permissions(dept_services, direct_roles, group_services, group_roles)
    dept = await dept_repo.get_by_id(user.department_id) if user.department_id else None

    audit_service.emit(
        "user.me", user_id, status="success", allowed=True, request_id=request_id,
        details={
            "username": user.username,
            "department_id": user.department_id,
            "platform_role": user.platform_role,
            "allowed_services_count": len(allowed_services),
        },
    )
    return _build_identity(user, dept.display_name if dept else None, allowed_services, service_roles)
