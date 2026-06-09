"""Бизнес-логика юзеров: CRUD, ban/unban (+ revoke сессий/PAT/bot-токенов), assign roles, reset password."""

from datetime import timezone

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import get_settings
from src.core.constants import PlatformRole, UserStatus
from src.core.exceptions import AuthenticationError, AuthorizationError, ConflictError, DomainValidationError, NotFoundError
from src.core.security import hash_password, mask_email, verify_password
from src.repositories.bans import BanRepository
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
    SessionEntry,
    SessionsListResponse,
    UserGroupWithRolesEntry,
    UserPermissionsResponse,
    UserResponse,
)
from src.services import _lockout, audit_service
from src.services._cache_invalidation import invalidate_identity_cache as _invalidate_identity_cache
from src.services._dept_guard import assert_dept_admin_target_dept
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
    include_banned: bool = False,
    status_filter: str | None = None,
) -> tuple[list[UserResponse], int]:
    """Глобальный список юзеров (страница). account_admin only.

    Возвращает `(страница, total)` — `total` идёт в `X-Total-Count`.

    `include_banned=True` снимает фильтр `is_active`; админ-list видит
    забаненных/заблокированных. `status_filter` (опционально) — пост-фильтр
    по `UserStatus` (`active`/`banned`/`blocked`). Если задан — `include_banned`
    автоматически считается True (иначе `banned`-фильтр давал бы пусто).
    """
    pagination = pagination or PaginationParams()
    user_repo = UserRepository(db)
    dept_repo = DepartmentRepository(db)
    effective_include_banned = include_banned or status_filter is not None
    users = await user_repo.list_all(
        limit=pagination.limit,
        offset=pagination.offset,
        include_banned=effective_include_banned,
        status_filter=status_filter,
    )
    total = await user_repo.count_active(
        include_banned=effective_include_banned,
        status_filter=status_filter,
    )
    dept_names = {d.id: d.display_name for d in await dept_repo.list_all()}
    audit_service.emit(
        "user.list", actor_id, status="success", request_id=request_id,
        details={
            "count": len(users),
            "total": total,
            "scope": "all",
            "include_banned": effective_include_banned,
            "status_filter": status_filter,
        },
    )
    return [_to_response(u, dept_names.get(u.department_id)) for u in users], total


async def list_users_by_department(
    db: AsyncSession,
    actor_id: str,
    actor_role: str | None,
    department_id: str,
    pagination: PaginationParams | None = None,
    request_id: str | None = None,
    include_banned: bool = False,
    status_filter: str | None = None,
) -> tuple[list[UserResponse], int]:
    """Юзеры одного отдела (страница). department_admin — только свой; account_admin — любой.

    `include_banned` / `status_filter` — см. `list_users`.
    """
    pagination = pagination or PaginationParams()
    user_repo = UserRepository(db)
    dept_repo = DepartmentRepository(db)

    dept = await dept_repo.get_by_id(department_id)
    if dept is None:
        raise NotFoundError(error_code="DEPARTMENT_NOT_FOUND", message=f"Department '{department_id}' not found")

    if actor_role == PlatformRole.DEPARTMENT_ADMIN:
        await assert_dept_admin_target_dept(
            user_repo, actor_id, department_id,
            error_code="DEPARTMENT_ACCESS_DENIED",
            message="department_admin can only view users in their own department",
        )

    effective_include_banned = include_banned or status_filter is not None
    users = await user_repo.list_by_department(
        department_id,
        limit=pagination.limit,
        offset=pagination.offset,
        include_banned=effective_include_banned,
        status_filter=status_filter,
    )
    total = await user_repo.count_by_department(
        department_id,
        include_banned=effective_include_banned,
        status_filter=status_filter,
    )
    audit_service.emit(
        "user.list", actor_id, status="success",
        details={
            "department_id": department_id,
            "count": len(users),
            "total": total,
            "scope": "department",
            "include_banned": effective_include_banned,
            "status_filter": status_filter,
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
        await assert_dept_admin_target_dept(
            user_repo, actor_id, department_id,
            error_code="DEPARTMENT_ACCESS_DENIED",
            message="department_admin can only create users in their own department",
        )

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
    # Платформенные роли без привязки к департаменту: account_admin —
    # глобальный админ платформы, loging_admin/loging_reader — централизованное
    # управление и чтение аудит-событий по всем департаментам.
    _platform_admins = {PR.ACCOUNT_ADMIN, PR.LOGING_ADMIN, PR.LOGING_READER}
    if platform_role not in _platform_admins and not department_id:
        raise DomainValidationError(error_code="MISSING_REQUIRED_FIELD", message="department_id is required for non-admin users")

    dept = await dept_repo.get_by_id(department_id) if department_id else None
    if department_id and dept is None:
        raise NotFoundError(error_code="DEPARTMENT_NOT_FOUND", message=f"Department '{department_id}' not found")

    if await user_repo.exists_username(username):
        raise ConflictError(error_code="USER_ALREADY_EXISTS", message=f"Username '{username}' is already taken")

    # `must_change_password=True`: dep_admin/account_admin создал юзера с
    # временным паролем (видимым в audit details — sanitizer заменит на
    # <PASSWORD>). До первой самостоятельной смены через POST /users/me/password
    # middleware заблокирует доступ ко всем остальным endpoint'ам.
    user = await user_repo.create(
        username=username,
        password_hash=hash_password(password),
        department_id=department_id,
        email=email,
        platform_role=platform_role,
        created_by=actor_id,
        must_change_password=True,
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
            # Один SELECT по (department, service) вместо N×exists. Под обычным
            # каталогом ролей экономия минорная, но симметрично с bulk_assign /
            # assign_bot_roles, где паттерн прижился раньше.
            defined = {
                r.role_name
                for r in await role_def_repo.list_active(department_id, svc_name)
            }
            for role in roles:
                if role not in defined:
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
            "email": mask_email(email),
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


async def _revoke_sessions_on_block(
    db: AsyncSession,
    user,
    actor_id: str,
    request_id: str | None,
) -> list[dict]:
    """Снести активные сессии и PAT при PATCH status → BLOCKED.

    Возвращает список pending-audit dict'ов (пустой — если revoke'ить было
    нечего ни там, ни там). Audit-emit + commit делает caller — мы только
    flush'им изменения в ту же транзакцию, чтобы один финальный
    `db.commit()` покрыл и status-update, и session/PAT-revoke (атомарно).

    PAT revoke'им под reason="user" (BLOCKED — административная блокировка
    юзера, не ban; reason `ban` зарезервирован за `ban_user`, чтобы
    `unban_user.reactivate_ban_revoked` не подхватил эти PAT'ы при
    последующей разблокировке через ACTIVE).
    """
    pending: list[dict] = []
    session_repo = SessionRepository(db)
    revoked_sessions = await session_repo.revoke_all_for_user(user.id)
    if revoked_sessions:
        pending.append({
            "action": "user.sessions_revoked_on_block",
            "actor_id": actor_id,
            "target_id": user.id,
            "target_type": "user",
            "details": {
                "target_username": user.username,
                "sessions_revoked": revoked_sessions,
                "source": "patch_user_status_blocked",
            },
            "request_id": request_id,
        })
    token_repo = TokenRepository(db)
    revoked_pats = await token_repo.revoke_all_for_user(user.id, reason="user")
    if revoked_pats:
        pending.append({
            "action": "user.pat_revoked_on_block",
            "actor_id": actor_id,
            "target_id": user.id,
            "target_type": "user",
            "details": {
                "target_username": user.username,
                "pat_revoked": revoked_pats,
                "source": "patch_user_status_blocked",
            },
            "request_id": request_id,
        })
    return pending


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
        await assert_dept_admin_target_dept(
            user_repo, actor_id, user.department_id,
            error_code="USER_UPDATE_FORBIDDEN",
            message="Cannot update user outside your department",
        )

    allowed_fields = {"email", "department_id", "status", "platform_role"}
    filtered = {k: v for k, v in updates.items() if k in allowed_fields and v is not None}
    if actor_role == PlatformRole.DEPARTMENT_ADMIN:
        filtered.pop("platform_role", None)

    # Cross-dept transfer = only account_admin. Guard выше сверял
    # `actor.dept == user.dept` (DA своего отдела может PATCH'ить юзера),
    # но не проверял целевой department_id — DA dept_alpha мог PATCH'ить
    # юзера `{"department_id": dept_beta}` и выкинуть юзера из своего же
    # отдела в чужой. Симметрия с `assign_roles` (роли — внутри dept'а
    # юзера) и `create_user` (DA создаёт только в своём отделе).
    requested_dept_id = filtered.get("department_id")
    if (
        requested_dept_id is not None
        and requested_dept_id != user.department_id
        and actor_role != PlatformRole.ACCOUNT_ADMIN
    ):
        raise AuthorizationError(
            error_code="USER_UPDATE_FORBIDDEN",
            message="Only account_admin can move users between departments",
        )

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
    # При переходе в BLOCKED через update_user активные сессии юзера тоже
    # должны умереть — иначе access-token живёт ещё ~10 минут (TTL), и
    # заблокированный юзер успевает походить по системе через уже выданный
    # JWT. Симметрия с веткой BANNED (`ban_user` revoke'ит сессии сам).
    pending_block_revoke_audit: list[dict] = []
    if new_status is not None and new_status != current_status:
        # ACTIVE↔BANNED разрешено только account_admin'у. POST /users/{id}/ban
        # и /unban защищены `AccountAdmin`-guard'ом; PATCH /users/{id} идёт
        # под `AnyAdmin` — без явной проверки department_admin мог бы
        # забанить/разбанить юзера через `status`-поле в обход guard'а.
        status_change_is_ban = new_status == UserStatus.BANNED
        status_change_is_unban = (
            new_status == UserStatus.ACTIVE and current_status == UserStatus.BANNED
        )
        if (status_change_is_ban or status_change_is_unban) and actor_role != PlatformRole.ACCOUNT_ADMIN:
            raise AuthorizationError(
                error_code="STATUS_CHANGE_REQUIRES_ACCOUNT_ADMIN",
                message="Only account_admin can ban or unban users via status change",
            )
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
            # BANNED → BLOCKED: `ban_user` уже revoke'нул сессии при
            # первоначальном бане. К моменту PATCH'а активных сессий обычно
            # нет (юзер был забанен). Зовём `revoke_all_for_user` всё равно —
            # идемпотентно, count=0 при пустом множестве. Если count > 0
            # (теоретически — оператор мог вручную восстановить сессию в БД),
            # эмитим audit.
            pending_block_revoke_audit = await _revoke_sessions_on_block(
                db, user, actor_id, request_id,
            )
            # Записываем сам status/is_active в filtered — общий
            # `user_repo.update` чуть ниже применит.
            filtered["status"] = new_status.value
            filtered["is_active"] = new_status == UserStatus.ACTIVE
        elif new_status == UserStatus.BLOCKED:
            # ACTIVE → BLOCKED (других вариантов сюда не доходит: BANNED → *
            # обработан выше). У ACTIVE-юзера могут быть живые сессии и
            # access-token'ы (TTL ~10 мин) — без revoke'а заблокированный
            # юзер продолжит ходить по системе до истечения JWT.
            pending_block_revoke_audit = await _revoke_sessions_on_block(
                db, user, actor_id, request_id,
            )
            filtered["status"] = new_status.value
            filtered["is_active"] = new_status == UserStatus.ACTIVE
        else:
            # Остальные переходы без ban-side-effects — мапим enum в строку и
            # руками синкаем is_active (только ACTIVE).
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
    pending_groups_purged_audit: dict | None = None
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

        # Симметрично с roles purge: членство юзера в группах старого отдела
        # после transfer'а оставляет за ним доступы и роли через
        # `GroupServiceAccess` / `GroupServiceRole` тех групп (privilege
        # retention). Удаляем memberships, привязанные к группам прежнего
        # dept_id, новый dept админ перевыдаст вручную.
        group_repo_local = GroupRepository(db)
        removed_group_ids = await group_repo_local.remove_user_memberships_in_department(
            user_id, old_dept_id,
        )
        if removed_group_ids:
            pending_groups_purged_audit = {
                "action": "user.groups_purged_on_transfer",
                "actor_id": actor_id,
                "target_id": user_id,
                "target_type": "user",
                "details": {
                    "target_username": user.username,
                    "old_dept_id": old_dept_id,
                    "new_dept_id": new_dept_id,
                    "removed_group_ids": removed_group_ids,
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
        or pending_block_revoke_audit
        or pending_roles_purged_audit is not None
        or pending_groups_purged_audit is not None
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
        or pending_block_revoke_audit
        or pending_roles_purged_audit is not None
        or pending_groups_purged_audit is not None
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
    for _evt in pending_block_revoke_audit:
        audit_service.emit(
            _evt["action"],
            _evt["actor_id"],
            target_id=_evt["target_id"],
            target_type=_evt["target_type"],
            details=_evt["details"],
            request_id=_evt["request_id"],
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
    if pending_groups_purged_audit is not None:
        audit_service.emit(
            pending_groups_purged_audit["action"],
            pending_groups_purged_audit["actor_id"],
            target_id=pending_groups_purged_audit["target_id"],
            target_type=pending_groups_purged_audit["target_type"],
            details=pending_groups_purged_audit["details"],
            request_id=pending_groups_purged_audit["request_id"],
        )

    # `user.update`-audit имеет смысл только если были реальные не-ban
    # изменения. Если PATCH нёс только status → BANNED/ACTIVE — audit уже
    # эмитнут (`user.ban`/`user.unban`), `user.update` дублировать не нужно.
    if filtered:
        # email — PII, в audit-trail у loging_reader полный домен+local не
        # нужен. Маскируем только для audit-snapshot, в БД сохраняется
        # полный email через `user_repo.update(**filtered)` выше.
        audit_changes = dict(filtered)
        if "email" in audit_changes:
            audit_changes["email"] = mask_email(audit_changes["email"])
        audit_service.emit(
            "user.update", actor_id, target_id=user_id, target_type="user",
            request_id=request_id,
            details={
                "username": user.username,
                "changes": audit_changes,
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

    # Cross-dept guard поднят выше `has_active_access` и role-каталога: иначе
    # error_code (`SERVICE_NOT_ALLOWED_FOR_DEPARTMENT` vs `INVALID_SERVICE_ROLE`)
    # давал бы DA из dept_alpha oracle на состояние dept_beta — есть ли у того
    # service_x и какие роли там определены. Зеркало `_check_can_manage` в
    # `service_role_service`: dept-isolation вперёд per-service существования.
    if actor_role == PlatformRole.DEPARTMENT_ADMIN:
        await assert_dept_admin_target_dept(
            user_repo, actor_id, user.department_id,
            error_code="USER_ROLE_UPDATE_FORBIDDEN",
            message="Cannot assign roles outside your department",
        )

    # Забаненному/выключенному юзеру навешивать роль нельзя: при unban'е они
    # резко становятся действующими (role-resurrection). Симметрично
    # `group_service.add_bot_member`, где `is_active=False` → 404.
    if not user.is_active:
        raise ConflictError(
            error_code="USER_INACTIVE",
            message="Cannot assign roles to inactive user",
        )

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
    *,
    actor_role: str | None = None,
    request_id: str | None = None,
) -> None:
    """Сбросить пароль юзеру + revoke все его активные сессии и все PAT'ы.

    Дополнительно сбрасывает identity-cache, чтобы кешированный access-token
    с `is_active=True` не пускал юзера до истечения TTL после смены пароля.
    """
    user_repo = UserRepository(db)
    session_repo = SessionRepository(db)
    token_repo = TokenRepository(db)

    user = await user_repo.get_by_id(user_id)
    if user is None:
        raise NotFoundError(error_code="USER_NOT_FOUND", message="User not found")

    # Cross-dept escalation guard симметрично с update_user / assign_roles:
    # DA одного отдела не должен сбрасывать пароль юзеру другого отдела
    # (это снесло бы его сессии/PAT и дало takeover после установки нового
    # пароля). Если actor_role не передан (legacy/service-level вызов),
    # резолвим из БД — иначе DA с не-переданной ролью обошёл бы guard.
    # Helper сам тянет actor'а и фейлит c ACTOR_VANISHED, если
    # JWT валидный, а самого юзера уже нет.
    if actor_role is None:
        _actor = await user_repo.get_by_id(actor_id)
        if _actor is not None:
            actor_role = _actor.platform_role
    if actor_role == PlatformRole.DEPARTMENT_ADMIN:
        await assert_dept_admin_target_dept(
            user_repo, actor_id, user.department_id,
            error_code="USER_RESET_PASSWORD_FORBIDDEN",
            message="department_admin can only reset passwords for users in their own department",
        )

    # Admin-flow без current_password-confirm: actor != target по определению
    # (cross-dept guard выше + endpoint-level `AnyAdmin`). Self-reset идёт
    # через отдельный `change_own_password` с обязательным old_password.

    # `must_change_password=True`: admin сбросил пароль чужому юзеру → юзер
    # обязан сменить пароль на свой при первом login'е, до доступа к остальным
    # endpoint'ам. Защита от ситуации «admin знает временный пароль, юзер о нём
    # не знает». Self-reset через `/me/password` ниже не доходит сюда — у того
    # отдельный handler, `must_change_password` там сбрасывается в False.
    await user_repo.update(
        user,
        password_hash=hash_password(new_password),
        must_change_password=True,
    )
    await session_repo.revoke_all_for_user(user_id)
    await token_repo.revoke_all_for_user(user_id)
    await db.commit()
    # Сессии и PAT'ы юзера сняты — identity-кэш может ещё нести `is_active=True`
    # и пускать ранее закэшированный access-token до TTL. Сбрасываем сразу.
    _invalidate_identity_cache(user_id)
    # actor_role в details: SIEM-у проще фильтровать по конкретному типу
    # actor'а, чем тянуть его из отдельного запроса в auth. Источники:
    #   * actor==target → "self" (admin сбрасывает себе пароль через
    #     admin-ручку — теоретически возможно, маркируем явно);
    #   * resolved platform_role → значение enum'а (account_admin /
    #     department_admin / loging_*);
    #   * actor_role не передан и в БД нет — "unknown" (не падаем, аудит
    #     всё равно эмитим, чтобы не потерять событие смены пароля).
    if actor_id == user_id:
        audit_actor_role = "self"
    elif actor_role:
        audit_actor_role = str(actor_role)
    else:
        audit_actor_role = "unknown"
    # new_password → sanitizer заменит на <PASSWORD>
    audit_service.emit(
        "user.password_reset", actor_id, target_id=user_id, target_type="user",
        details={
            "target_username": user.username,
            "new_password": new_password,
            "sessions_revoked": True,
            "tokens_revoked": True,
            "actor_role": audit_actor_role,
        },
        request_id=request_id,
    )


_ADMIN_PLATFORM_ROLES = frozenset(
    {
        PlatformRole.ACCOUNT_ADMIN,
        PlatformRole.DEPARTMENT_ADMIN,
        PlatformRole.LOGING_ADMIN,
        PlatformRole.LOGING_READER,
    }
)


async def change_own_password(
    db: AsyncSession,
    user_id: str,
    old_password: str,
    new_password: str,
    request_id: str | None = None,
) -> None:
    """Self-reset пароля юзером с обязательным подтверждением текущего пароля.

    Отдельная от admin-`reset_password` ручка: actor == target, поэтому нужно
    знание `old_password` (иначе угон access-токена даёт перманентный takeover
    через смену пароля без подтверждения). admin→admin сценарий покрывается
    тем же путём — account_admin меняет себе пароль через `/me/password`, а не
    через admin-ручку.

    Поведение:
        * 404 USER_NOT_FOUND — JWT валиден, но юзер удалён (race).
        * 422 SAME_PASSWORD — new_password совпадает с old (через verify, не
          через сравнение строк, чтобы политика «нельзя ставить тот же пароль»
          работала даже когда policy чуть отличается).
        * 401 INVALID_OLD_PASSWORD + инкремент `failed_login_attempts` — та же
          lockout-шкала, что и в `/login`, чтобы brute-force старого пароля
          через `/me/password` упирался в тот же `ACCOUNT_TEMPORARILY_LOCKED`.
        * При успехе — новый хэш Argon2id, revoke всех активных сессий
          (включая ту, которой пришёл вызов: пользователь должен залогиниться
          заново и получить свежий refresh), revoke всех собственных PAT'ов
          (`revoked_reason="admin_reset"`), identity-cache reset, audit
          `user.self_password_reset` (CRITICAL) с `pat_revoked_count`.
          Bot-токены НЕ трогаем — бот принадлежит департаменту, не юзеру
          (см. obsidian/Ролевая модель.md): смена пароля одного человека
          не должна валить CI/integration отдела.
    """
    user_repo = UserRepository(db)
    session_repo = SessionRepository(db)
    token_repo = TokenRepository(db)

    user = await user_repo.get_by_id(user_id)
    if user is None:
        raise NotFoundError(error_code="USER_NOT_FOUND", message="User not found")

    settings = get_settings()

    # Lockout: истёкший lockout сначала сбрасываем, активный — отбиваем 429.
    # Лимит общий с /login: иначе атакующий, получивший access-токен и
    # пытающийся угадать старый пароль для смены, обходил бы счётчик логина.
    if await _lockout.release_if_expired(user_repo, user):
        await db.commit()
    _lockout.assert_not_locked(user)

    if not verify_password(old_password, user.password_hash):
        await _lockout.register_failure(
            user_repo,
            user,
            max_attempts=settings.max_failed_login_attempts,
            lockout_minutes=settings.lockout_minutes,
        )
        # Commit до raise — иначе get_db()-rollback стирает инкремент,
        # та же грабля, что и в verify_password_with_lockout.
        await db.commit()
        # Аудит неудачи: SIEM должен видеть попытки смены пароля с неверным
        # старым (потенциальный признак угона access-токена).
        audit_service.emit(
            "user.self_password_reset",
            user_id,
            target_id=user_id,
            target_type="user",
            status="failure",
            allowed=False,
            details={
                "reason": "invalid_old_password",
                "caller_is_admin": user.platform_role in _ADMIN_PLATFORM_ROLES,
                "actor_role": "self",
            },
            request_id=request_id,
        )
        raise AuthenticationError(
            error_code="INVALID_OLD_PASSWORD",
            message="Old password is incorrect",
        )

    # Сброс счётчика на успешном verify'е (как в /login).
    await _lockout.register_success(user_repo, user)

    # Same-password-as-old guard. Pydantic не может это проверить (хэш в БД),
    # поэтому 422 кидаем здесь. До смены/revoke — чтобы юзер не выкинулся из
    # сессий впустую.
    if verify_password(new_password, user.password_hash):
        # Commit reset_failed_attempts до raise: иначе get_db()-rollback
        # вернёт stale-счётчик после валидного old_password (юзер
        # доказал владение паролем).
        await db.commit()
        raise DomainValidationError(
            error_code="SAME_PASSWORD",
            message="New password must differ from the current one",
        )

    # Сбрасываем `must_change_password` — юзер сам сменил пароль, форсить
    # повторную смену больше не нужно. Если флаг и так был False (обычный
    # self-reset) — no-op. Отдельный INFO-audit `user.must_change_password_cleared`
    # эмитим ниже, чтобы SIEM видел снятие force-flag отдельным сигналом
    # (полезно для отчётов «сколько новых юзеров активировались»).
    was_forced = bool(user.must_change_password)
    await user_repo.update(
        user,
        password_hash=hash_password(new_password),
        must_change_password=False,
    )
    await session_repo.revoke_all_for_user(user_id)
    # PAT-revoke на смене собственного пароля: до фикса юзер с угнанным
    # access'ом мог менять пароль, а ранее созданные PAT'ы атакующего
    # переживали ротацию — perm-takeover через PAT, выписанный до смены
    # пароля. `reason="admin_reset"` — самый близкий из существующих
    # значений `RevokeReason`-литерала (password-reset driven revoke);
    # `reactivate_ban_revoked` смотрит только на `reason="ban"`, так что
    # эти PAT'ы остаются навсегда мёртвыми. Боты НЕ трогаем — они
    # принадлежат департаменту, не юзеру.
    pat_revoked_count = await token_repo.revoke_all_for_user(
        user_id, reason="admin_reset"
    )
    await db.commit()
    # Identity-cache: без сброса закэшированный access-token продолжит
    # пускать юзера на /me и introspect до истечения TTL даже после revoke
    # сессий (revoke бьёт refresh, не access). Симметрично admin-reset'у.
    _invalidate_identity_cache(user_id)
    audit_service.emit(
        "user.self_password_reset",
        user_id,
        target_id=user_id,
        target_type="user",
        details={
            "caller_is_admin": user.platform_role in _ADMIN_PLATFORM_ROLES,
            "sessions_revoked": True,
            "tokens_revoked": True,
            "pat_revoked_count": pat_revoked_count,
            "actor_role": "self",
            "must_change_password_was_forced": was_forced,
        },
        request_id=request_id,
    )
    # Отдельный сигнал «force-flag снят» — только если он реально был. Без
    # отдельного event'а SIEM'у пришлось бы парсить details внутри
    # `user.self_password_reset`, а отчёт «сколько новых юзеров активировались»
    # хочется строить простым group-by по action'у.
    if was_forced:
        audit_service.emit(
            "user.must_change_password_cleared",
            user_id,
            target_id=user_id,
            target_type="user",
            details={"source": "self_password_reset"},
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
    ban_id = new_ban.id
    # ── PAT revoke ───────────────────────────────────────────────────────────
    # Без revoke'а PAT'ы юзера переживали ban: introspect показывал
    # `is_banned=True`, но live-токен проходил по hash'у — стянутый PAT
    # сохранял доступ к остальным сервисам (config_service, docker registry).
    # `revoked_reason="ban"` нужен для `unban_user`: реактивирует именно
    # ban-revoked PAT, а не вручную отозванные через DELETE /tokens/{id}.
    pat_revoked_count = await token_repo.revoke_all_for_user(user_id, reason="ban")
    # by-design: bots survive ban. Ботов и их токены при бане владельца не
    # трогаем — бот живёт отдельной identity'ю, привязан к отделу, и
    # выпадение владельца не должно валить CI/integrations отдела. После
    # бана dept_admin перевыпустит токен через `POST /bots/{id}/tokens`,
    # если это нужно по compliance-причинам.

    pending_audit = {
        "action": "user.ban",
        "actor_id": actor_id,
        "target_id": user_id,
        "target_type": "user",
        "details": {
            "target_username": user.username,
            "ban_id": ban_id,
            "ban_type": ban_type,
            "reason": reason,
            "expires_at": expires_at.isoformat() if expires_at else None,
            # Метрики revoke'а в audit-trail — мониторинг по `user.ban` сможет
            # отслеживать «ban стоил N токенов» для compliance-отчётов.
            "pat_revoked": True,
            "pat_revoked_count": pat_revoked_count,
            # Боты намеренно остаются живыми (см. комментарий выше). Поля с
            # bot-counter'ами оставляем нулевыми для обратной совместимости
            # SIEM-правил, ожидающих эти ключи в `user.ban`. Явный
            # `bots_policy` — машиночитаемое объявление политики, чтобы
            # SIEM-правила могли отличать «ноль ботов было» от «по политике
            # не трогаем» без сравнения с историей.
            "bot_tokens_revoked": 0,
            "owned_bots_count": 0,
            "bots_policy": "no_auto_revoke",
        },
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


async def hard_delete_user(
    db: AsyncSession,
    actor_id: str,
    actor_username: str | None,
    user_id: str,
    reason: str,
    request_id: str | None = None,
) -> dict:
    """Hard-delete юзера.

    Жёсткое удаление: row в `users` сносится, ORM-cascade сметает
    `UserServiceRole`, `Session`, `PersonalAccessToken`, `Ban`,
    `UserGroupMembership` (см. `User.relationship(..., cascade="all,
    delete-orphan")`). Бот-аккаунты юзера остаются (бот — dept-owned
    entity, не наследуется за создателем; FK `bots.created_by` —
    nullable string без referential constraint, см. модель).

    Защита:
        * Отказ удалять последнего активного `account_admin`'а — иначе
          платформа теряет admin-управление. Сравнение «один и единственный
          оставшийся» делается через `count_active_account_admins` ПОД
          row-lock'ом самого user'а (FOR UPDATE), чтобы конкурентные
          hard-delete'ы двух разных админов не схлопнули счётчик до нуля.

    Cascade-revoke перед DELETE:
        Хотя ORM-cascade всё равно снесёт сессии/PAT, мы делаем явный
        `revoke_all_for_user` для них ДО `db.delete(user)`. Это нужно,
        чтобы audit-trail увидел `pat_revoked_count` отдельным числом
        (важно для compliance: «при удалении сняли N токенов»), и чтобы
        identity-cache был honestly invalidated одной и той же логикой,
        что в ban/reset-password. Bot-токены ботов, которые создал этот
        юзер, НЕ трогаем — симметрично `user.ban` policy.

    Audit:
        `user.hard_deleted` CRITICAL. `details.reason` — обязательное
        пользовательское обоснование (compliance). После commit'а вызываем
        `secret_service_client.notify_user_deleted` best-effort — secret_service
        блокирует personal cred'ы юзера. Callback не блокирует ответ:
        ошибка best-effort, попадёт в `secret_lifecycle.notify_failed` audit.

    Возвращает dict со сводкой (sessions/PAT revoked) для дополнительной
    отдачи endpoint'у, если потребуется.
    """
    from src.services import secret_service_client

    user_repo = UserRepository(db)
    session_repo = SessionRepository(db)
    token_repo = TokenRepository(db)

    user = await user_repo.get_for_update(user_id)
    if user is None:
        raise NotFoundError(error_code="USER_NOT_FOUND", message="User not found")

    target_username = user.username
    target_department_id = user.department_id

    # ── Last-admin guard ────────────────────────────────────────────────────
    # Снос последнего активного account_admin'а оставит инсталляцию без
    # admin-доступа — bootstrap'ить нового admin'а можно только через
    # manual DB-INSERT / `bootstrap_service`, что requires-ops-intervention.
    # Лучше отбить на endpoint'е: 422 + actionable error code.
    if user.platform_role == PlatformRole.ACCOUNT_ADMIN:
        remaining = await user_repo.count_active_account_admins()
        if remaining <= 1:
            raise DomainValidationError(
                error_code="LAST_ACCOUNT_ADMIN",
                message="Cannot hard-delete the last active account_admin",
            )

    # Снимаем счётчики ДО delete'а, чтобы попасть в audit-detail.
    sessions_revoked = await session_repo.revoke_all_for_user(user_id)
    pat_revoked = await token_repo.revoke_all_for_user(user_id, reason="hard_delete")

    await user_repo.delete(user)
    await db.commit()
    # Кэш мог нести идентичность удалённого юзера; новый запрос с тем же
    # access-токеном должен сразу провалиться в `_resolve_user_identity`
    # (нет такого user'а в БД), а не висеть до TTL.
    _invalidate_identity_cache(user_id)

    audit_service.emit(
        "user.hard_deleted", actor_id, target_id=user_id, target_type="user",
        details={
            "target_username": target_username,
            "target_department_id": target_department_id,
            "reason": reason,
            "sessions_revoked": sessions_revoked,
            "pat_revoked_count": pat_revoked,
        },
        request_id=request_id,
    )

    # secret_service notify — best-effort; ошибки уходят в audit
    # `secret_lifecycle.notify_failed` внутри клиента, наружу не пробрасываем.
    try:
        await secret_service_client.notify_user_deleted(
            user_id=user_id,
            actor_id=actor_id,
            actor_username=actor_username,
        )
    except Exception as exc:
        import logging as _logging
        _logging.getLogger(__name__).warning(
            "secret_service notify failed (hard_delete_user): %s", exc,
        )

    return {
        "user_id": user_id,
        "sessions_revoked": sessions_revoked,
        "pat_revoked_count": pat_revoked,
    }


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
    # Симметрия с manual `unban_user`: реактивируем PAT'ы, отозванные именно
    # этим ban'ом (`revoked_reason="ban"` И `revoked_at >= ban.banned_at`).
    # До фикса auto-unban оставлял PAT'ы revoked, а manual unban воскрешал —
    # разница в поведении между двумя путями того же lifecycle-перехода.
    token_repo = TokenRepository(db)
    pat_reactivated = await token_repo.reactivate_ban_revoked(
        user.id, since=ban.banned_at,
    )
    await db.commit()
    # Симметрия с manual `unban_user`: кэш ещё держит `is_banned=True` /
    # `status=BANNED`, и следующий запрос с access-токеном попадал бы под
    # ban-guard вплоть до истечения TTL. Сбрасываем сразу.
    _invalidate_identity_cache(user.id)
    audit_service.emit(
        "user.unban", None, target_id=user.id, target_type="user",
        details={
            "target_username": user.username,
            "previous_ban_id": ban.id,
            "previous_ban_reason": ban.reason,
            "reason": "ban_expired",
            "source": "auto",
            "expires_at": expires_at_aware.isoformat(),
            "pat_reactivated": pat_reactivated,
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
    """Helper: эмит `user.permissions_view` с `status="failure"`.

    Действие одно, разделяет статус — `success`/`failure`. SIEM
    `status=failure` ловит попытки заглянуть в чужие права. Конвенция
    единая по auth_service (см. bot_service `_check_can_manage_bot_or_audit`).
    """
    audit_service.emit(
        "user.permissions_view",
        actor_id,
        target_id=target_id,
        target_type="user",
        status="failure",
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
    `user.permissions_view status=failure + reason`.
    """
    user_repo = UserRepository(db)
    dept_repo = DepartmentRepository(db)
    role_repo = RoleRepository(db)
    group_repo = GroupRepository(db)

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

    # is_account_admin и is_dept_admin взаимоисключающие (platform_role
    # один на юзера), поэтому `not is_account_admin` лишний. Self
    # фильтруем явно — admin может смотреть свою же строку, не упираясь
    # в случайный mismatch department_id.
    if is_dept_admin and not is_self and identity.department_id != target.department_id:
        _emit_permissions_denied(
            actor_id=identity.user_id,
            target_id=user_id,
            reason="cross_department_user",
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
        # `include_groups=False` — endpoint строит свой raw-список групп
        # с per-service ролями выше; flatten `<svc>.<role>` тут не нужен.
        allowed_services, service_roles, _groups = await collect_user_permissions(
            db, target, include_groups=False,
        )
    allowed_services = sorted(allowed_services)
    service_roles = {
        svc: sorted(roles) for svc, roles in service_roles.items()
    }

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


# ── /users/me/sessions ───────────────────────────────────────────────────────


def _session_to_entry(sess, current_session_id: str | None) -> SessionEntry:
    return SessionEntry(
        session_id=sess.id,
        created_at=sess.created_at,
        last_used_at=sess.last_used_at,
        expires_at=sess.expires_at,
        ip_address=sess.ip_address,
        user_agent=sess.user_agent,
        is_current=(current_session_id is not None and sess.id == current_session_id),
    )


async def list_sessions(
    db: AsyncSession,
    user_id: str,
    current_session_id: str | None = None,
    request_id: str | None = None,
) -> SessionsListResponse:
    """Список активных refresh-сессий юзера для UI «Active devices».

    `current_session_id` — `sid` claim из текущего access-токена. Если есть
    и совпадает с одной из сессий — её `is_current=True`. Если в JWT нет
    `sid` (legacy-токен до фичи) — None и все entries без current-флага.

    Audit `user.sessions_listed` INFO с count'ом активных сессий.
    """
    session_repo = SessionRepository(db)
    sessions = await session_repo.list_active_for_user(user_id)
    items = [_session_to_entry(s, current_session_id) for s in sessions]
    audit_service.emit(
        "user.sessions_listed",
        user_id,
        target_id=user_id,
        target_type="user",
        status="success",
        details={"count": len(items)},
        request_id=request_id,
    )
    return SessionsListResponse(items=items, total=len(items))


async def revoke_sessions(
    db: AsyncSession,
    user_id: str,
    except_session_id: str | None = None,
    request_id: str | None = None,
) -> int:
    """Revoke все активные refresh-сессии юзера, опционально кроме одной.

    Симметрично admin `reset_password` по части revoke-сессий, но без сброса
    пароля и PAT — это user-инициированное «выйти со всех устройств». PAT
    не трогаем намеренно: они представляют отдельную identity (CI/боты).

    `except_session_id` — id той сессии, которую оставить (обычно `sid` из
    JWT). None или несуществующий id — снести всё. Identity-cache
    инвалидируем, чтобы закэшированный access-token со старого устройства
    не пускал юзера на /me и introspect до истечения TTL.
    """
    session_repo = SessionRepository(db)
    revoked = await session_repo.revoke_all_for_user(
        user_id, except_session_id=except_session_id
    )
    await db.commit()
    _invalidate_identity_cache(user_id)
    audit_service.emit(
        "user.sessions_revoked_all",
        user_id,
        target_id=user_id,
        target_type="user",
        details={
            "revoked_count": revoked,
            "except_session_id": except_session_id,
            "except_current": except_session_id is not None,
        },
        request_id=request_id,
    )
    return revoked


async def revoke_one_session(
    db: AsyncSession,
    user_id: str,
    session_id: str,
    current_session_id: str | None = None,
    request_id: str | None = None,
) -> int:
    """Целевой logout одной сессии юзера.

    404 SESSION_NOT_FOUND — сессия не принадлежит юзеру, неактивна, или не
    существует. Намеренно не отличаем «нет» от «чужая» — иначе endpoint
    становится session-id-enumeration oracle'ом между юзерами.

    Возвращает 1 при успехе.
    """
    session_repo = SessionRepository(db)
    sess = await session_repo.get_by_id(session_id)
    if sess is None or sess.user_id != user_id or not sess.is_active:
        raise NotFoundError(
            error_code="SESSION_NOT_FOUND",
            message="Session not found or already revoked",
        )

    await session_repo.revoke(sess)
    await db.commit()
    _invalidate_identity_cache(user_id)
    audit_service.emit(
        "user.session_revoked_one",
        user_id,
        target_id=user_id,
        target_type="user",
        details={
            "session_id": session_id,
            "was_current": current_session_id is not None and session_id == current_session_id,
        },
        request_id=request_id,
    )
    return 1
