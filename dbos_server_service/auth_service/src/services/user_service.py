"""Бизнес-логика юзеров: CRUD, ban/unban (+ revoke сессий/PAT/bot-токенов), assign roles, reset password."""

from datetime import timezone

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.constants import PlatformRole, UserStatus
from src.core.exceptions import AuthorizationError, ConflictError, DomainValidationError, NotFoundError
from src.core.security import hash_password
from src.repositories.bans import BanRepository
from src.repositories.bot_tokens import BotTokenRepository
from src.repositories.bots import BotRepository
from src.repositories.departments import DepartmentRepository
from src.repositories.groups import GroupRepository
from src.repositories.roles import RoleRepository
from src.repositories.service_role_definitions import ServiceRoleDefinitionRepository
from src.repositories.sessions import SessionRepository
from src.repositories.tokens import TokenRepository
from src.repositories.users import UserRepository
from src.schemas.auth import IdentityContext
from src.schemas.users import (
    DirectServiceRoleEntry,
    GroupServiceAccessEntry,
    GroupServiceRoleEntry,
    UserGroupWithRolesEntry,
    UserPermissionsResponse,
    UserResponse,
)
from src.services import audit_service
from src.services._cache_invalidation import invalidate_identity_cache as _invalidate_identity_cache
from src.services.auth_service import collect_user_permissions
from src.utils.pagination import PaginationParams
from src.utils.time import utcnow


def _to_response(user, dept_name: str | None) -> UserResponse:
    """ORM-юзер → UserResponse DTO."""
    return UserResponse(
        user_id=user.id,
        username=user.username,
        email=user.email,
        department_id=user.department_id,
        department_name=dept_name,
        status=user.status,
        platform_role=user.platform_role,
        is_active=user.is_active,
        created_at=user.created_at,
    )


async def list_users(
    db: AsyncSession,
    actor_id: str,
    pagination: PaginationParams | None = None,
    request_id: str | None = None,
) -> tuple[list[UserResponse], int]:
    """Глобальный список юзеров (страница). account_admin only.

    Возвращает `(страница, total)` — `total` идёт в `X-Total-Count`.
    """
    pagination = pagination or PaginationParams()
    user_repo = UserRepository(db)
    dept_repo = DepartmentRepository(db)
    users = await user_repo.list_all(limit=pagination.limit, offset=pagination.offset)
    total = await user_repo.count_active()
    dept_names = {d.id: d.display_name for d in await dept_repo.list_all()}
    audit_service.emit(
        "user.list", actor_id, status="success", request_id=request_id,
        details={"count": len(users), "total": total, "scope": "all"},
    )
    return [_to_response(u, dept_names.get(u.department_id)) for u in users], total


async def list_users_by_department(
    db: AsyncSession,
    actor_id: str,
    actor_role: str | None,
    department_id: str,
    pagination: PaginationParams | None = None,
    request_id: str | None = None,
) -> tuple[list[UserResponse], int]:
    """Юзеры одного отдела (страница). department_admin — только свой; account_admin — любой."""
    pagination = pagination or PaginationParams()
    user_repo = UserRepository(db)
    dept_repo = DepartmentRepository(db)

    dept = await dept_repo.get_by_id(department_id)
    if dept is None:
        raise NotFoundError(error_code="DEPARTMENT_NOT_FOUND", message=f"Department '{department_id}' not found")

    if actor_role == PlatformRole.DEPARTMENT_ADMIN:
        actor = await user_repo.get_by_id(actor_id)
        if actor and actor.department_id != department_id:
            raise AuthorizationError(
                error_code="DEPARTMENT_ACCESS_DENIED",
                message="department_admin can only view users in their own department",
            )

    users = await user_repo.list_by_department(
        department_id, limit=pagination.limit, offset=pagination.offset
    )
    total = await user_repo.count_by_department(department_id)
    audit_service.emit(
        "user.list", actor_id, status="success",
        details={
            "department_id": department_id,
            "count": len(users),
            "total": total,
            "scope": "department",
        },
        request_id=request_id,
    )
    return [_to_response(u, dept.display_name) for u in users], total


async def create_user(
    db: AsyncSession,
    actor_id: str,
    actor_role: str | None,
    username: str,
    password: str,
    department_id: str,
    email: str | None = None,
    platform_role: str | None = None,
    initial_roles: list | None = None,
    request_id: str | None = None,
) -> UserResponse:
    """Создать юзера + опционально выдать initial_roles в одной транзакции."""
    dept_repo = DepartmentRepository(db)
    user_repo = UserRepository(db)

    if actor_role == PlatformRole.DEPARTMENT_ADMIN:
        actor = await user_repo.get_by_id(actor_id)
        if actor and actor.department_id != department_id:
            raise AuthorizationError(error_code="DEPARTMENT_ACCESS_DENIED", message="department_admin can only create users in their own department")

    # Только account_admin раздаёт платформенные роли. Без этой проверки
    # department_admin мог бы создать юзера с platform_role=account_admin и
    # подняться до полного доступа к платформе. Поле приходит уже валидным
    # enum'ом (PlatformRole), так что достаточно отбить любой не-None у
    # не-account_admin'а.
    if actor_role != PlatformRole.ACCOUNT_ADMIN and platform_role is not None:
        raise AuthorizationError(
            error_code="PLATFORM_ROLE_ASSIGNMENT_DENIED",
            message="Only account_admin can assign platform roles",
        )

    from src.core.constants import PlatformRole as PR
    _platform_admins = {PR.ACCOUNT_ADMIN, PR.LOGING_ADMIN}
    if platform_role not in _platform_admins and not department_id:
        raise DomainValidationError(error_code="MISSING_REQUIRED_FIELD", message="department_id is required for non-admin users")

    dept = await dept_repo.get_by_id(department_id) if department_id else None
    if department_id and dept is None:
        raise NotFoundError(error_code="DEPARTMENT_NOT_FOUND", message=f"Department {department_id} not found")

    if await user_repo.exists_username(username):
        raise ConflictError(error_code="USER_ALREADY_EXISTS", message=f"Username '{username}' is already taken")

    user = await user_repo.create(
        username=username,
        password_hash=hash_password(password),
        department_id=department_id,
        email=email,
        platform_role=platform_role,
        created_by=actor_id,
    )

    if initial_roles:
        role_def_repo = ServiceRoleDefinitionRepository(db)
        role_repo = RoleRepository(db)
        for assignment in initial_roles:
            svc_name = assignment.service_name if hasattr(assignment, "service_name") else assignment["service_name"]
            roles = assignment.roles if hasattr(assignment, "roles") else assignment["roles"]
            if not await dept_repo.has_active_access(department_id, svc_name):
                raise AuthorizationError(
                    error_code="SERVICE_NOT_ALLOWED_FOR_DEPARTMENT",
                    message=f"Service '{svc_name}' is not allowed for this department",
                )
            for role in roles:
                if not await role_def_repo.exists(department_id, svc_name, role):
                    raise DomainValidationError(
                        error_code="INVALID_SERVICE_ROLE",
                        message=(
                            f"Role '{role}' is not defined for service '{svc_name}' "
                            f"in department '{department_id}'"
                        ),
                    )
            await role_repo.set_roles(user.id, svc_name, roles, assigned_by=actor_id)

    await db.commit()
    # password передаётся в details — sanitizer заменит на <PASSWORD>. Полный
    # аудит «что админ задавал», без утечки самого секрета.
    audit_service.emit(
        "user.create", actor_id, target_id=user.id, target_type="user",
        request_id=request_id,
        details={
            "username": username,
            "password": password,
            "email": email,
            "department_id": department_id,
            "department_name": dept.display_name if dept else None,
            "platform_role": platform_role,
            "initial_roles": [
                {
                    "service_name": a.service_name if hasattr(a, "service_name") else a["service_name"],
                    "roles": list(a.roles if hasattr(a, "roles") else a["roles"]),
                }
                for a in (initial_roles or [])
            ],
        },
    )
    return _to_response(user, dept.display_name if dept else None)


async def update_user(
    db: AsyncSession,
    actor_id: str,
    actor_role: str | None,
    user_id: str,
    updates: dict,
    request_id: str | None = None,
) -> UserResponse:
    """Patch юзера + lifecycle-делегация на ban_user/unban_user при изменении status."""
    user_repo = UserRepository(db)
    dept_repo = DepartmentRepository(db)

    user = await user_repo.get_by_id(user_id)
    if user is None:
        raise NotFoundError(error_code="USER_NOT_FOUND", message="User not found")

    if actor_role == PlatformRole.DEPARTMENT_ADMIN:
        actor = await user_repo.get_by_id(actor_id)
        if actor and actor.department_id != user.department_id:
            raise AuthorizationError(error_code="USER_UPDATE_FORBIDDEN", message="Cannot update user outside your department")

    allowed_fields = {"email", "department_id", "status", "platform_role"}
    filtered = {k: v for k, v in updates.items() if k in allowed_fields and v is not None}
    if actor_role == PlatformRole.DEPARTMENT_ADMIN:
        filtered.pop("platform_role", None)

    # ── Status transition: делегируем ban/unban side-effects ──────────────────
    # ACTIVE↔BANNED идёт через `ban_user`/`unban_user` — они эмитят правильный
    # audit, создают/деактивируют `Ban`-record и revoke-ят сессии. Иначе PATCH
    # status≠BANNED обходил всю ban-логику. Для BLOCKED и no-op'ов — обычный
    # update; Pydantic enum уже отсёк произвольные строки.
    new_status_raw = filtered.get("status")
    new_status: UserStatus | None = None
    if new_status_raw is not None:
        new_status = (
            UserStatus(new_status_raw)
            if not isinstance(new_status_raw, UserStatus)
            else new_status_raw
        )

    current_status = UserStatus(user.status) if not isinstance(user.status, UserStatus) else user.status

    # `user.ban`/`user.unban`-audit эмитим строго после финального commit'а —
    # иначе crash между ban_user.commit и плоским update'ом остальных полей
    # давал partial state (забанен, но email не обновлён, `user.update`-audit
    # не отправлен). Сейчас один commit покрывает обе мутации.
    pending_ban_audit: dict | None = None
    # BANNED → {BLOCKED, ACTIVE-через-PATCH}: надо деактивировать активный
    # Ban-record, иначе `User.status=BLOCKED`, но `Ban.is_active=True`. Без
    # этого: BLOCKED-юзер «забанен» в `GET /users`, unban-эндпоинт упадёт на
    # `BAN_ALREADY_ACTIVE`, `auto_unban_if_expired` молча no-op'нет.
    # BANNED → ACTIVE уже покрыт через `unban_user` ниже — здесь только → BLOCKED.
    pending_ban_deactivation_audit: dict | None = None
    if new_status is not None and new_status != current_status:
        if new_status == UserStatus.BANNED:
            # Делегируем — `ban_user(commit=False)` подготавливает изменения
            # (status, is_active, Ban-record, revoke sessions) и возвращает
            # данные для `user.ban`-audit'а, который эмитим в самом конце.
            pending_ban_audit = await ban_user(
                db=db,
                actor_id=actor_id,
                user_id=user_id,
                ban_type="permanent",
                reason="status_change_via_patch",
                request_id=request_id,
                commit=False,
            )
            filtered.pop("status", None)
        elif new_status == UserStatus.ACTIVE and current_status == UserStatus.BANNED:
            # Зеркало для unban.
            pending_ban_audit = await unban_user(
                db=db,
                actor_id=actor_id,
                user_id=user_id,
                request_id=request_id,
                commit=False,
            )
            filtered.pop("status", None)
        elif current_status == UserStatus.BANNED and new_status == UserStatus.BLOCKED:
            # BANNED → BLOCKED: деактивируем активный Ban, иначе status=BLOCKED
            # но Ban.is_active=True. Не делегируем в `unban_user` (был бы
            # race-window ACTIVE между unban и block, плюс `user.unban`-audit
            # вводил бы SIEM в заблуждение). Свой action — чище.
            ban_repo = BanRepository(db)
            existing_ban = await ban_repo.get_active_ban(user_id)
            if existing_ban is not None:
                transitioned = await ban_repo.deactivate(
                    existing_ban, unbanned_by=actor_id,
                )
                if transitioned:
                    # Складываем pending-audit; emit'нем после commit'а в
                    # унифицированной точке ниже.
                    pending_ban_deactivation_audit = {
                        "action": "user.ban_deactivated_via_status_change",
                        "actor_id": actor_id,
                        "target_id": user_id,
                        "target_type": "user",
                        "details": {
                            "target_username": user.username,
                            "previous_ban_id": existing_ban.id,
                            "previous_ban_reason": existing_ban.reason,
                            "new_status": new_status.value,
                            "source": "patch_user_status",
                        },
                        "request_id": request_id,
                    }
            # Записываем сам status/is_active в filtered — общий
            # `user_repo.update` чуть ниже применит.
            filtered["status"] = new_status.value
            filtered["is_active"] = new_status == UserStatus.ACTIVE
        else:
            # BLOCKED-из-ACTIVE или любой другой переход без ban-side-effects
            # — мапим enum в строку и руками синкаем is_active (только ACTIVE).
            filtered["status"] = new_status.value
            filtered["is_active"] = new_status == UserStatus.ACTIVE
    elif new_status is not None:
        # Идемпотентный no-op (status уже совпадает) — нормализуем значение,
        # is_active оставляем согласованным.
        filtered["status"] = new_status.value
        filtered["is_active"] = new_status == UserStatus.ACTIVE

    # ── Department transfer: purge service-roles ────────────────────────────
    # При смене dept'а старые `UserServiceRole` указывают на сервисы прежнего
    # отдела — `_merge_permissions` INTERSECT их уже отфильтровывает из
    # effective view, но строки остаются в БД (инвариант «роль ⊆ сервисы
    # отдела» нарушен). Деактивируем все active-роли; admin переназначит при
    # необходимости. Старые dept_id/new_dept_id запоминаем для audit.
    old_dept_id = user.department_id
    pending_roles_purged_audit: dict | None = None
    new_dept_id = filtered.get("department_id")
    if new_dept_id is not None and new_dept_id != old_dept_id:
        role_repo_local = RoleRepository(db)
        purged_count = await role_repo_local.deactivate_all_for_user(user_id)
        if purged_count:
            pending_roles_purged_audit = {
                "action": "user.roles_purged_on_transfer",
                "actor_id": actor_id,
                "target_id": user_id,
                "target_type": "user",
                "details": {
                    "target_username": user.username,
                    "from_department_id": old_dept_id,
                    "to_department_id": new_dept_id,
                    "roles_purged_count": purged_count,
                },
                "request_id": request_id,
            }

    # Применяем остаток filtered (email/department_id/platform_role или
    # status=BLOCKED) поверх той же сессии — ban_user/unban_user уже сделали
    # `flush()`, но НЕ `commit()`. SQLAlchemy identity-map отдаёт нам тот же
    # `user`-объект, который они мутировали, так что `setattr(user, "email",
    # ...)` не перетирает status/is_active.
    if filtered:
        await user_repo.update(user, **filtered)

    # Один финальный commit покрывает и ban/unban-side-effects, и остальные
    # поля. Если упадём здесь — всё откатывается единым SAVEPOINT'ом сессии,
    # никакого partial state.
    if (
        pending_ban_audit is not None
        or pending_ban_deactivation_audit is not None
        or pending_roles_purged_audit is not None
        or filtered
    ):
        await db.commit()

    # Privilege-changing PATCH (status/platform_role/department_id) меняет
    # authoritative identity → invalidate identity-кэш. `ban_user`/`unban_user`
    # делают invalidate сами при commit=True, но мы тут с commit=False —
    # наша ответственность.
    privilege_fields = {"status", "platform_role", "department_id"}
    if (
        pending_ban_audit is not None
        or pending_ban_deactivation_audit is not None
        or pending_roles_purged_audit is not None
        or (filtered and privilege_fields & set(filtered.keys()))
    ):
        _invalidate_identity_cache(user_id)

    dept = await dept_repo.get_by_id(user.department_id)

    # Audit-emit строго ПОСЛЕ commit'а — иначе при rollback мы бы отправили
    # fake `user.ban` без реальной мутации в БД. Порядок: сначала ban/unban
    # (lifecycle-переход), потом user.update (остальные поля), чтобы SIEM
    # видел причинно-следственный порядок.
    if pending_ban_audit is not None:
        audit_service.emit(
            pending_ban_audit["action"],
            pending_ban_audit["actor_id"],
            target_id=pending_ban_audit["target_id"],
            target_type=pending_ban_audit["target_type"],
            details=pending_ban_audit["details"],
            request_id=pending_ban_audit["request_id"],
        )
    if pending_ban_deactivation_audit is not None:
        audit_service.emit(
            pending_ban_deactivation_audit["action"],
            pending_ban_deactivation_audit["actor_id"],
            target_id=pending_ban_deactivation_audit["target_id"],
            target_type=pending_ban_deactivation_audit["target_type"],
            details=pending_ban_deactivation_audit["details"],
            request_id=pending_ban_deactivation_audit["request_id"],
        )
    if pending_roles_purged_audit is not None:
        audit_service.emit(
            pending_roles_purged_audit["action"],
            pending_roles_purged_audit["actor_id"],
            target_id=pending_roles_purged_audit["target_id"],
            target_type=pending_roles_purged_audit["target_type"],
            details=pending_roles_purged_audit["details"],
            request_id=pending_roles_purged_audit["request_id"],
        )

    # `user.update`-audit имеет смысл только если были реальные не-ban
    # изменения. Если PATCH нёс только status → BANNED/ACTIVE — audit уже
    # эмитнут (`user.ban`/`user.unban`), `user.update` дублировать не нужно.
    if filtered:
        audit_service.emit(
            "user.update", actor_id, target_id=user_id, target_type="user",
            request_id=request_id,
            details={
                "username": user.username,
                "changes": filtered,
                "fields_changed": sorted(filtered.keys()),
            },
        )
    return _to_response(user, dept.display_name if dept else None)


async def assign_roles(
    db: AsyncSession,
    actor_id: str,
    actor_role: str | None,
    user_id: str,
    service_name: str,
    roles: list[str],
    request_id: str | None = None,
) -> None:
    """Replace ролей юзера для сервиса. Идемпотентно по `(user, service)`."""
    user_repo = UserRepository(db)
    dept_repo = DepartmentRepository(db)
    role_repo = RoleRepository(db)
    role_def_repo = ServiceRoleDefinitionRepository(db)

    user = await user_repo.get_by_id(user_id)
    if user is None:
        raise NotFoundError(error_code="USER_NOT_FOUND", message="User not found")

    if not await dept_repo.has_active_access(user.department_id, service_name):
        raise AuthorizationError(
            error_code="SERVICE_NOT_ALLOWED_FOR_DEPARTMENT",
            message=f"Service '{service_name}' is not allowed for this department",
            details={"service_name": service_name},
        )

    for role in roles:
        if not await role_def_repo.exists(user.department_id, service_name, role):
            raise DomainValidationError(
                error_code="INVALID_SERVICE_ROLE",
                message=(
                    f"Role '{role}' is not defined for service '{service_name}' "
                    f"in department '{user.department_id}'"
                ),
                details={"service_name": service_name, "role": role},
            )

    if actor_role == PlatformRole.DEPARTMENT_ADMIN:
        actor = await user_repo.get_by_id(actor_id)
        if actor and actor.department_id != user.department_id:
            raise AuthorizationError(error_code="USER_ROLE_UPDATE_FORBIDDEN", message="Cannot assign roles outside your department")

    await role_repo.set_roles(user_id, service_name, roles, assigned_by=actor_id)
    await db.commit()
    # Смена ролей меняет `service_roles`/`allowed_services` в IdentityContext
    # — invalidate кэш. И для эскалации, и для revoke'а.
    _invalidate_identity_cache(user_id)
    audit_service.emit(
        "user.roles_assign", actor_id, target_id=user_id, target_type="user",
        details={
            "target_username": user.username,
            "service_name": service_name,
            "roles": list(roles),
            "department_id": user.department_id,
        },
        request_id=request_id,
    )


async def reset_password(
    db: AsyncSession,
    actor_id: str,
    user_id: str,
    new_password: str,
    request_id: str | None = None,
) -> None:
    """Сбросить пароль юзеру + revoke его сессии и PAT."""
    user_repo = UserRepository(db)
    session_repo = SessionRepository(db)
    token_repo = TokenRepository(db)

    user = await user_repo.get_by_id(user_id)
    if user is None:
        raise NotFoundError(error_code="USER_NOT_FOUND", message="User not found")

    # ── Cross-department escalation guard ─────────────────────────────────────
    # `AnyAdmin` пускает оба типа админов; без этой проверки department_admin
    # dept_a мог бы сбросить пароль юзеру dept_b (плюс снести его сессии/PAT)
    # и получить escalation через угадывание user_id. `update_user` делает
    # то же самое — симметрия.
    # TODO: `actor_role` пока не прокидывается из endpoint'а — тянем из БД
    # дополнительным SELECT (`get_current_identity` уже подгрузил actor.row
    # в session identity-map, так что обычно бесплатно).
    actor = await user_repo.get_by_id(actor_id)
    if (
        actor is not None
        and actor.platform_role == PlatformRole.DEPARTMENT_ADMIN
        and actor.department_id != user.department_id
    ):
        raise AuthorizationError(
            error_code="DEPARTMENT_ISOLATION",
            message="department_admin can only reset passwords for users in their own department",
        )

    # TODO: self-reset с `current_password`-confirm и защита
    # account_admin → account_admin (нужно знание текущего пароля для смены
    # чужого account_admin-пароля). Сейчас schema несёт только new_password.

    await user_repo.update(user, password_hash=hash_password(new_password))
    await session_repo.revoke_all_for_user(user_id)
    await token_repo.revoke_all_for_user(user_id)
    await db.commit()
    # new_password → sanitizer заменит на <PASSWORD>
    audit_service.emit(
        "user.password_reset", actor_id, target_id=user_id, target_type="user",
        details={
            "target_username": user.username,
            "new_password": new_password,
            "sessions_revoked": True,
            "tokens_revoked": True,
        },
        request_id=request_id,
    )


async def ban_user(
    db: AsyncSession,
    actor_id: str,
    user_id: str,
    ban_type: str,
    reason: str | None,
    expires_at=None,
    request_id: str | None = None,
    commit: bool = True,
) -> dict | None:
    """Забанить юзера.

    `commit=True` (default, endpoint-путь) — мы владеем транзакцией: пишем
    изменения, `db.commit()`, эмитим `user.ban`. `commit=False` (делегация
    из `update_user`) — commit и audit-emit на caller'е: возвращаем dict с
    pending audit'ом, чтобы один финальный commit покрыл и ban-side-effects,
    и остальные поля PATCH'а.
    """
    user_repo = UserRepository(db)
    ban_repo = BanRepository(db)
    session_repo = SessionRepository(db)
    token_repo = TokenRepository(db)
    bot_repo = BotRepository(db)
    bot_token_repo = BotTokenRepository(db)

    user = await user_repo.get_by_id(user_id)
    if user is None:
        raise NotFoundError(error_code="USER_NOT_FOUND", message="User not found")

    existing_ban = await ban_repo.get_active_ban(user_id)
    if existing_ban:
        raise ConflictError(error_code="BAN_ALREADY_ACTIVE", message="User already has an active ban")

    # `is_active` дублирует `status != BANNED` — синхронизируем оба поля,
    # иначе PAT-introspect (`authorization_service.introspect`) и
    # `repositories/users.list_all`/`list_by_department` будут давать неверный
    # ответ. Аналог `bot_service.update_bot` (status/is_active sync).
    await user_repo.update(user, status=UserStatus.BANNED, is_active=False)
    new_ban = await ban_repo.create(user_id=user_id, banned_by=actor_id, ban_type=ban_type, reason=reason, expires_at=expires_at)
    await session_repo.revoke_all_for_user(user_id)
    # ── PAT revoke ───────────────────────────────────────────────────────────
    # Без revoke'а PAT'ы юзера переживали ban: introspect показывал
    # `is_banned=True`, но live-токен проходил по hash'у — стянутый PAT
    # сохранял доступ к остальным сервисам (config_service, docker registry).
    # `revoked_reason="ban"` нужен для `unban_user`: реактивирует именно
    # ban-revoked PAT, а не вручную отозванные через DELETE /tokens/{id}.
    pat_revoked_count = await token_repo.revoke_all_for_user(user_id, reason="ban")
    # ── Bot tokens revoke ────────────────────────────────────────────────────
    # `BotAccount.created_by` — единственная user→bot связь в текущей схеме.
    # Если `created_by` пуст (бот старый или сделан account_admin'ом без UI)
    # — бот в выборку не попадает. Выбираем ботов узко по `created_by` и
    # отзываем их токены одним bulk-UPDATE вместо per-bot/per-token цикла.
    owned_bots = await bot_repo.list_by_creator(user_id)
    revoked_bot_tokens = await bot_token_repo.revoke_all_for_bots(
        [bot.id for bot in owned_bots]
    )

    pending_audit = {
        "action": "user.ban",
        "actor_id": actor_id,
        "target_id": user_id,
        "target_type": "user",
        "details": {
            "target_username": user.username,
            "ban_type": ban_type,
            "reason": reason,
            "expires_at": expires_at.isoformat() if expires_at else None,
            # Метрики revoke'а в audit-trail — мониторинг по `user.ban` сможет
            # отслеживать «ban стоил N токенов» для compliance-отчётов.
            "pat_revoked": True,
            # Явный counter, парный с `bot_tokens_revoked`. Поле `pat_revoked:
            # True` оставлено для обратной совместимости тестов/мониторинга.
            "pat_revoked_count": pat_revoked_count,
            "bot_tokens_revoked": revoked_bot_tokens,
            "owned_bots_count": len(owned_bots),
        },  # TODO: добавить severity=CRITICAL явно — сейчас тянем из дефолта SERVICE_EVENTS
        "request_id": request_id,
    }

    if not commit:
        return pending_audit

    await db.commit()
    # Сбросить cached identity сразу — иначе ban задержится на TTL (~5s),
    # «банят → следующий запрос 401» ломается и забаненный успевает
    # закончить ongoing операции.
    _invalidate_identity_cache(user_id)
    audit_service.emit(
        pending_audit["action"],
        pending_audit["actor_id"],
        target_id=pending_audit["target_id"],
        target_type=pending_audit["target_type"],
        details=pending_audit["details"],
        request_id=pending_audit["request_id"],
    )
    return None


async def unban_user(
    db: AsyncSession,
    actor_id: str,
    user_id: str,
    request_id: str | None = None,
    commit: bool = True,
) -> dict | None:
    """Снять бан. Симметрично `ban_user` — см. его docstring про `commit=False`.

    Реактивирует только ban-revoked PAT'ы текущего ban'а
    (`revoked_reason="ban"` + `revoked_at >= ban.banned_at`). Старые ban'ы
    не воскрешают — юзер мог сам зачистить токены между bans.
    """
    user_repo = UserRepository(db)
    ban_repo = BanRepository(db)
    token_repo = TokenRepository(db)

    user = await user_repo.get_by_id(user_id)
    if user is None:
        raise NotFoundError(error_code="USER_NOT_FOUND", message="User not found")

    ban = await ban_repo.get_active_ban(user_id)
    if ban is None:
        raise NotFoundError(error_code="BAN_NOT_FOUND", message="No active ban found")

    # Operator-initiated unban: deactivate через CAS. Если ban-row уже
    # переключён другим worker'ом между `get_active_ban` и `deactivate`
    # (узкое окно, но возможно при гонке с auto-unban), обрабатываем как
    # «no active ban» — 404. Operator-initiated double-unban и до фикса
    # возвращал 404 — контракт сохраняем.
    transitioned = await ban_repo.deactivate(ban, unbanned_by=actor_id)
    if not transitioned:
        raise NotFoundError(error_code="BAN_NOT_FOUND", message="No active ban found")
    # Зеркально к `ban_user` — синхронизируем `is_active` со `status`.
    await user_repo.update(user, status=UserStatus.ACTIVE, is_active=True)

    # ── Re-activate ban-revoked PAT ─────────────────────────────────────────
    # Только `revoked_reason="ban"` И `revoked_at >= ban.banned_at`: если
    # юзер был banned → unbanned → banned → сейчас unban, воскрешаем PAT
    # только последнего ban'а. PAT'ы первого ban'а имеют `revoked_at` раньше
    # второго `ban.banned_at` — фильтр их отбрасывает.
    pat_reactivated = await token_repo.reactivate_ban_revoked(
        user_id, since=ban.banned_at,
    )

    pending_audit = {
        "action": "user.unban",
        "actor_id": actor_id,
        "target_id": user_id,
        "target_type": "user",
        "details": {
            "target_username": user.username,
            "previous_ban_id": ban.id,
            "previous_ban_reason": ban.reason,
            # Чтобы мониторинг по `user.unban` видел «PAT'ы юзера возвращены».
            "pat_reactivated": pat_reactivated,
        },
        "request_id": request_id,
    }

    if not commit:
        return pending_audit

    await db.commit()
    # Кэш может ещё нести `is_banned=True`/`status=BANNED` — сбрасываем,
    # чтобы next request увидел ACTIVE.
    _invalidate_identity_cache(user_id)
    audit_service.emit(
        pending_audit["action"],
        pending_audit["actor_id"],
        target_id=pending_audit["target_id"],
        target_type=pending_audit["target_type"],
        details=pending_audit["details"],
        request_id=pending_audit["request_id"],
    )
    return None


async def auto_unban_if_expired(
    db: AsyncSession,
    user,
    request_id: str | None = None,
) -> bool:
    """Снять temporary-ban с истёкшим `expires_at` just-in-time.

    Вызывается из `login` и других точек, где мы знаем `user.status == BANNED`.
    Возвращает `True`, если auto-unban сработал, и `False` иначе.

    Поведение:
    - Если активного `Ban`-record нет — расхождение `status` vs `Ban`-таблица,
      возвращаем `False` (let caller обработать как обычный ban). Это
      defensive: модель такого не предполагает, но мы не должны схлопывать
      статус молча, если состояние в БД противоречивое.
    - Если `Ban.expires_at` отсутствует (permanent) или ещё не наступил —
      возвращаем `False` (ban остаётся в силе).
    - Если `expires_at < now` — деактивируем `Ban`, обновляем `User.status`/
      `is_active`, коммитим, эмитим `user.unban` audit с `details.reason =
      "ban_expired"` и `actor_id=None` (system-инициированное действие).

    Audit-обоснование: используем существующий action `user.unban`, потому
    что это **тот же lifecycle-переход** (BANNED → ACTIVE), и мониторинг на
    `user.unban` обязан видеть и автоматические разбаны тоже. Различие
    operator vs. system отражается через `actor_id=None` + `details.source`,
    а не через отдельный action — иначе пришлось бы регистрировать новый
    event в `audit_events.py` и обновлять loging rules, что несоразмерно
    для cleanup-операции с тем же эффектом.
    """
    if user.status != UserStatus.BANNED:
        return False

    ban_repo = BanRepository(db)
    user_repo = UserRepository(db)

    ban = await ban_repo.get_active_ban(user.id)
    if ban is None:
        return False
    if ban.expires_at is None:
        return False

    # `Ban.expires_at` хранится в UTC; нормализуем naive→aware для сравнения.
    expires_at_aware = (
        ban.expires_at
        if ban.expires_at.tzinfo is not None
        else ban.expires_at.replace(tzinfo=timezone.utc)
    )
    if expires_at_aware >= utcnow():
        return False

    # CAS-deactivate: `UPDATE bans SET is_active=False WHERE id=:id AND
    # is_active=True RETURNING id`. RETURNING пуст → другой worker уже снял
    # ban (второй login на том же истёкшем ban). Silently bail — без commit'а
    # и без audit'а; иначе `user.unban source=auto` шёл бы дважды.
    # `unbanned_by=None` = system-unban (колонка nullable; фильтр
    # `unbanned_by IS NULL` корректно отделяет auto-unban'ы).
    transitioned = await ban_repo.deactivate(ban, unbanned_by=None)
    if not transitioned:
        # Чужой worker уже сделал unban. Не коммитим, не аудитим — возвращаем
        # True, чтобы caller (login) перечитал user-инстанс и продолжил
        # обычный login-flow: state корректный, ban больше не активен.
        return True

    await user_repo.update(user, status=UserStatus.ACTIVE, is_active=True)
    await db.commit()
    audit_service.emit(
        "user.unban", None, target_id=user.id, target_type="user",
        details={
            "target_username": user.username,
            "previous_ban_id": ban.id,
            "previous_ban_reason": ban.reason,
            "reason": "ban_expired",
            "source": "auto",
            "expires_at": expires_at_aware.isoformat(),
        },
        request_id=request_id,
    )
    return True


# ── GET /users/{id}/permissions ──────────────────────────────────────────────


def _emit_permissions_denied(
    *,
    actor_id: str | None,
    target_id: str,
    reason: str,
    department_id: str | None,
    request_id: str | None,
) -> None:
    """Helper: эмит `user.permissions_view` с `status="denied"`.

    Действие одно, разделяет статус — `success`/`denied`. SIEM
    `status=denied` ловит попытки заглянуть в чужие права.
    """
    audit_service.emit(
        "user.permissions_view",
        actor_id,
        target_id=target_id,
        target_type="user",
        status="denied",
        allowed=False,
        department_id=department_id,
        details={"reason": reason},
        request_id=request_id,
    )


async def get_user_permissions(
    db: AsyncSession,
    user_id: str,
    identity: IdentityContext,
    request_id: str | None = None,
) -> UserPermissionsResponse:
    """Полный снимок прав юзера для UI / admin overview.

    Access guard:

    * **account_admin** — любой юзер (cross-dept by design, симметрично
      `list_users` / `list_user_groups`).
    * **department_admin** — только юзеры своего отдела
      (`identity.department_id == target.department_id`). Иначе 403
      `DEPARTMENT_ACCESS_DENIED`.
    * **Сам юзер** — может смотреть себя (`identity.user_id == user_id`).
    * Иначе — 403 `PERMISSION_DENIED`.

    404 `USER_NOT_FOUND` отдаётся только account_admin'у и владельцу. Для
    department_admin'а cross-dept тоже отдаёт 404 — иначе endpoint становится
    ID-enumeration oracle'ом. Для не-admin'а — 403, без раскрытия наличия
    user_id.

    На успех — `user.permissions_view status=success`. На denied —
    `user.permissions_view status=denied + reason`.
    """
    user_repo = UserRepository(db)
    dept_repo = DepartmentRepository(db)
    role_repo = RoleRepository(db)
    group_repo = GroupRepository(db)
    role_def_repo = ServiceRoleDefinitionRepository(db)

    # ── Step 1: access guard ──────────────────────────────────────────────
    is_account_admin = identity.platform_role == PlatformRole.ACCOUNT_ADMIN
    is_dept_admin = identity.platform_role == PlatformRole.DEPARTMENT_ADMIN
    is_self = identity.user_id == user_id

    if not (is_account_admin or is_dept_admin or is_self):
        # Обычный юзер смотрит на чужого — 403 без DB look-up, чтобы не
        # давать ID-enumeration oracle (существует ли target? — не скажем).
        _emit_permissions_denied(
            actor_id=identity.user_id,
            target_id=user_id,
            reason="not_self_not_admin",
            department_id=identity.department_id,
            request_id=request_id,
        )
        raise AuthorizationError(
            error_code="PERMISSION_DENIED",
            message="Cannot view permissions of another user",
        )

    target = await user_repo.get_by_id(user_id)
    if target is None:
        # account_admin / self — честный 404. Dept_admin: на cross-dept тоже
        # 404, иначе oracle (403 → существует в чужом dept, 404 → нет).
        _emit_permissions_denied(
            actor_id=identity.user_id,
            target_id=user_id,
            reason="user_not_found",
            department_id=identity.department_id,
            request_id=request_id,
        )
        raise NotFoundError(
            error_code="USER_NOT_FOUND",
            message="User not found",
        )

    if is_dept_admin and not is_account_admin and not is_self:
        # account_admin + self уже отфильтровались выше; здесь явная ветка
        # только для dept_admin (с проверкой is_self, чтобы admin, смотрящий
        # сам себя, не упёрся в случайный mismatch department_id).
        if identity.department_id != target.department_id:
            _emit_permissions_denied(
                actor_id=identity.user_id,
                target_id=user_id,
                reason="cross_department",
                department_id=identity.department_id,
                request_id=request_id,
            )
            raise AuthorizationError(
                error_code="DEPARTMENT_ACCESS_DENIED",
                message=(
                    "department_admin can only view permissions for users "
                    "in their own department"
                ),
            )

    # ── Step 2: collect data ──────────────────────────────────────────────
    dept = (
        await dept_repo.get_by_id(target.department_id)
        if target.department_id
        else None
    )

    # Direct service-roles: raw `UserServiceRole`-строки. Часть строк может
    # ссылаться на сервисы, к которым отдел/группы юзера лишились access —
    # отдаём как есть (UI должен видеть DB-state, effective layer ниже уже
    # отфильтрован INTERSECT'ом). JOIN с `service_role_definitions` не нужен:
    # `service_name` и `role` лежат прямо в `UserServiceRole`.
    direct_assignments = await role_repo.list_active_assignments(target.id)
    direct_service_roles = [
        DirectServiceRoleEntry(
            service_name=row.service_name,
            role_name=row.role,
            assigned_at=row.assigned_at,
            assigned_by=row.assigned_by,
        )
        for row in direct_assignments
    ]

    # Сортировка для стабильного UI/тестов: по (service_name, role_name).
    direct_service_roles.sort(key=lambda r: (r.service_name, r.role_name))

    # Groups + per-group services/roles. Каждая membership → группа → её
    # service_accesses (active) + service_roles (active). Группы, их access и
    # роли тянем тремя batch-выборками по списку group_id, а не 3 запросами
    # на каждую группу.
    memberships = await group_repo.list_user_groups(target.id)
    member_group_ids = [m.group_id for m in memberships]
    active_groups_by_id = {
        g.id: g for g in await group_repo.list_active_by_ids(member_group_ids)
    }
    access_by_group = await group_repo.list_service_access_for_groups(member_group_ids)
    roles_by_group = await group_repo.list_roles_for_groups(member_group_ids)
    groups: list[UserGroupWithRolesEntry] = []
    for m in memberships:
        grp = active_groups_by_id.get(m.group_id)
        if grp is None:
            # Stale membership на soft-deleted группу — скип. UI не должен
            # видеть «призрак» удалённой группы.
            continue
        access_list = access_by_group.get(grp.id, [])
        role_rows = roles_by_group.get(grp.id, [])
        groups.append(
            UserGroupWithRolesEntry(
                group_id=grp.id,
                group_name=grp.name,
                display_name=grp.display_name,
                department_id=grp.department_id,
                joined_at=m.added_at,
                service_accesses=sorted(
                    [
                        GroupServiceAccessEntry(service_name=a.service_name)
                        for a in access_list
                    ],
                    key=lambda x: x.service_name,
                ),
                service_roles=sorted(
                    [
                        GroupServiceRoleEntry(
                            service_name=r.service_name, role_name=r.role
                        )
                        for r in role_rows
                    ],
                    key=lambda x: (x.service_name, x.role_name),
                ),
            )
        )
    groups.sort(key=lambda g: (g.department_id, g.group_name))

    # Effective view через `collect_user_permissions` (с INTERSECT).
    # account_admin'у — пустой effective layer, как в IdentityContext: их
    # роли implicit через platform_role, а не через service_roles. Иначе UI
    # бы подумал, что у админа доступ только к подмножеству сервисов.
    if target.platform_role == PlatformRole.ACCOUNT_ADMIN:
        allowed_services: list[str] = []
        service_roles: dict[str, list[str]] = {}
    else:
        allowed_services, service_roles = await collect_user_permissions(
            db, target
        )
    allowed_services = sorted(allowed_services)
    service_roles = {
        svc: sorted(roles) for svc, roles in service_roles.items()
    }

    # `role_def_repo` пока не нужен, но оставлен — v2 endpoint'а может
    # включать display_name'ы ролей из `service_role_definitions`.
    del role_def_repo  # explicit suppression — see comment above.

    # ── Step 3: emit success audit + return ────────────────────────────────
    audit_service.emit(
        "user.permissions_view",
        identity.user_id,
        target_id=user_id,
        target_type="user",
        status="success",
        allowed=True,
        department_id=identity.department_id,
        details={
            "target_username": target.username,
            "target_department_id": target.department_id,
            "direct_roles_count": len(direct_service_roles),
            "groups_count": len(groups),
            "allowed_services_count": len(allowed_services),
        },
        request_id=request_id,
    )

    return UserPermissionsResponse(
        user_id=target.id,
        username=target.username,
        department_id=target.department_id,
        department_name=dept.display_name if dept else None,
        platform_role=(
            PlatformRole(target.platform_role)
            if target.platform_role
            else None
        ),
        is_active=target.is_active,
        is_banned=target.status == UserStatus.BANNED,
        status=UserStatus(target.status),
        direct_service_roles=direct_service_roles,
        groups=groups,
        allowed_services=allowed_services,
        service_roles=service_roles,
    )
