"""User management workflows."""

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.constants import PlatformRole, UserStatus
from src.core.exceptions import AuthorizationError, ConflictError, DomainValidationError, NotFoundError
from src.core.security import hash_password
from src.repositories.bans import BanRepository
from src.repositories.departments import DepartmentRepository
from src.repositories.roles import RoleRepository
from src.repositories.service_role_definitions import ServiceRoleDefinitionRepository
from src.repositories.sessions import SessionRepository
from src.repositories.tokens import TokenRepository
from src.repositories.users import UserRepository
from src.schemas.users import UserResponse
from src.services import audit_service
from src.utils.time import utcnow


def _to_response(user, dept_name: str | None) -> UserResponse:
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
    request_id: str | None = None,
) -> list[UserResponse]:
    user_repo = UserRepository(db)
    dept_repo = DepartmentRepository(db)
    users = await user_repo.list_all()
    dept_names = {d.id: d.display_name for d in await dept_repo.list_all()}
    audit_service.emit("user.list", actor_id, status="success", request_id=request_id)
    return [_to_response(u, dept_names.get(u.department_id)) for u in users]


async def list_users_by_department(
    db: AsyncSession,
    actor_id: str,
    actor_role: str | None,
    department_id: str,
    request_id: str | None = None,
) -> list[UserResponse]:
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

    users = await user_repo.list_by_department(department_id)
    audit_service.emit("user.list", actor_id, status="success",
                       details={"department_id": department_id}, request_id=request_id)
    return [_to_response(u, dept.display_name) for u in users]


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
    dept_repo = DepartmentRepository(db)
    user_repo = UserRepository(db)

    if actor_role == PlatformRole.DEPARTMENT_ADMIN:
        actor = await user_repo.get_by_id(actor_id)
        if actor and actor.department_id != department_id:
            raise AuthorizationError(error_code="DEPARTMENT_ACCESS_DENIED", message="department_admin can only create users in their own department")

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
                if not await role_def_repo.exists(svc_name, role):
                    raise DomainValidationError(
                        error_code="INVALID_SERVICE_ROLE",
                        message=f"Role '{role}' is not defined for service '{svc_name}'",
                    )
            await role_repo.set_roles(user.id, svc_name, roles, assigned_by=actor_id)

    await db.commit()
    audit_service.emit("user.create", actor_id, target_id=user.id, target_type="user", request_id=request_id)
    return _to_response(user, dept.display_name if dept else None)


async def update_user(
    db: AsyncSession,
    actor_id: str,
    actor_role: str | None,
    user_id: str,
    updates: dict,
    request_id: str | None = None,
) -> UserResponse:
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
    await user_repo.update(user, **filtered)
    await db.commit()

    dept = await dept_repo.get_by_id(user.department_id)
    audit_service.emit("user.update", actor_id, target_id=user_id, target_type="user", request_id=request_id)
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
        if not await role_def_repo.exists(service_name, role):
            raise DomainValidationError(
                error_code="INVALID_SERVICE_ROLE",
                message=f"Role '{role}' is not defined for service '{service_name}'",
                details={"service_name": service_name, "role": role},
            )

    if actor_role == PlatformRole.DEPARTMENT_ADMIN:
        actor = await user_repo.get_by_id(actor_id)
        if actor and actor.department_id != user.department_id:
            raise AuthorizationError(error_code="USER_ROLE_UPDATE_FORBIDDEN", message="Cannot assign roles outside your department")

    await role_repo.set_roles(user_id, service_name, roles, assigned_by=actor_id)
    await db.commit()
    audit_service.emit("user.roles_assign", actor_id, target_id=user_id, target_type="user", details={"service_name": service_name, "roles": roles}, request_id=request_id)


async def reset_password(
    db: AsyncSession,
    actor_id: str,
    user_id: str,
    new_password: str,
    request_id: str | None = None,
) -> None:
    user_repo = UserRepository(db)
    session_repo = SessionRepository(db)
    token_repo = TokenRepository(db)

    user = await user_repo.get_by_id(user_id)
    if user is None:
        raise NotFoundError(error_code="USER_NOT_FOUND", message="User not found")

    await user_repo.update(user, password_hash=hash_password(new_password))
    await session_repo.revoke_all_for_user(user_id)
    await token_repo.revoke_all_for_user(user_id)
    await db.commit()
    audit_service.emit("user.password_reset", actor_id, target_id=user_id, target_type="user", request_id=request_id)


async def ban_user(
    db: AsyncSession,
    actor_id: str,
    user_id: str,
    ban_type: str,
    reason: str | None,
    expires_at=None,
    request_id: str | None = None,
) -> None:
    user_repo = UserRepository(db)
    ban_repo = BanRepository(db)
    session_repo = SessionRepository(db)

    user = await user_repo.get_by_id(user_id)
    if user is None:
        raise NotFoundError(error_code="USER_NOT_FOUND", message="User not found")

    existing_ban = await ban_repo.get_active_ban(user_id)
    if existing_ban:
        raise ConflictError(error_code="BAN_ALREADY_ACTIVE", message="User already has an active ban")

    await user_repo.update(user, status=UserStatus.BANNED)
    await ban_repo.create(user_id=user_id, banned_by=actor_id, ban_type=ban_type, reason=reason, expires_at=expires_at)
    await session_repo.revoke_all_for_user(user_id)
    await db.commit()
    audit_service.emit("user.ban", actor_id, target_id=user_id, target_type="user", details={"reason": reason}, request_id=request_id)


async def unban_user(
    db: AsyncSession,
    actor_id: str,
    user_id: str,
    request_id: str | None = None,
) -> None:
    user_repo = UserRepository(db)
    ban_repo = BanRepository(db)

    user = await user_repo.get_by_id(user_id)
    if user is None:
        raise NotFoundError(error_code="USER_NOT_FOUND", message="User not found")

    ban = await ban_repo.get_active_ban(user_id)
    if ban is None:
        raise NotFoundError(error_code="BAN_NOT_FOUND", message="No active ban found")

    await ban_repo.deactivate(ban, unbanned_by=actor_id)
    await user_repo.update(user, status=UserStatus.ACTIVE)
    await db.commit()
    audit_service.emit("user.unban", actor_id, target_id=user_id, target_type="user", request_id=request_id)
