"""Бизнес-логика пользовательских групп: CRUD groups + членство + group service-access/role."""

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.constants import PlatformRole
from src.core.exceptions import AuthorizationError, ConflictError, NotFoundError
from src.repositories.groups import GroupRepository
from src.repositories.service_role_definitions import ServiceRoleDefinitionRepository
from src.repositories.services import ServiceRepository
from src.repositories.users import UserRepository
from src.schemas.groups import (
    GroupResponse, GroupRoleResponse, GroupServiceAccessResponse,
    MemberResponse, UserGroupsResponse,
)
from src.services import audit_service


def _invalidate_identity_cache(user_id: str) -> None:
    """Сбросить identity-кэш юзера после изменения членства в группе.
    Lazy import — `dependencies.auth` тянет audit-context, циклы.
    """
    try:
        from src.dependencies.auth import invalidate_identity_cache_for_user
        invalidate_identity_cache_for_user(user_id)
    except ImportError:
        pass


def _require_admin(identity) -> None:
    """Гард — требует account_admin. Жёсткий вариант, для глобальных операций."""
    if identity.platform_role != PlatformRole.ACCOUNT_ADMIN:
        raise AuthorizationError(error_code="ROLE_REQUIRED", message="account_admin role required")


def _grp_response(grp) -> GroupResponse:
    """ORM-группа → GroupResponse DTO."""
    return GroupResponse(
        id=grp.id,
        department_id=grp.department_id,
        name=grp.name,
        display_name=grp.display_name,
        description=grp.description,
        is_active=grp.is_active,
        created_at=grp.created_at,
        created_by=grp.created_by,
    )


# ── Group CRUD ────────────────────────────────────────────────────────────────

async def list_groups(db: AsyncSession, identity, request_id=None) -> list[GroupResponse]:
    """Список активных групп. account_admin only."""
    _require_admin(identity)
    repo = GroupRepository(db)
    return [_grp_response(g) for g in await repo.list_active()]


async def create_group(
    db: AsyncSession, identity, department_id: str, name: str, display_name: str,
    description: str | None, request_id=None,
) -> GroupResponse:
    """Создать группу внутри отдела.

    account_admin — любой отдел; department_admin — только свой.
    """
    if identity.platform_role == PlatformRole.DEPARTMENT_ADMIN:
        if identity.department_id != department_id:
            raise AuthorizationError(
                error_code="DEPARTMENT_FORBIDDEN",
                message="department_admin can only create groups in their own department",
            )
    elif identity.platform_role != PlatformRole.ACCOUNT_ADMIN:
        raise AuthorizationError(error_code="ROLE_REQUIRED", message="Admin role required")

    from src.repositories.departments import DepartmentRepository
    dept_repo = DepartmentRepository(db)
    if await dept_repo.get_by_id(department_id) is None:
        raise NotFoundError(
            error_code="DEPARTMENT_NOT_FOUND",
            message=f"Department '{department_id}' not found",
        )

    repo = GroupRepository(db)
    if await repo.get_by_name(department_id, name):
        raise ConflictError(
            error_code="GROUP_ALREADY_EXISTS",
            message=f"Group '{name}' already exists in department '{department_id}'",
        )
    grp = await repo.create(
        department_id=department_id,
        name=name,
        display_name=display_name,
        description=description,
        created_by=identity.user_id,
    )
    await db.commit()
    audit_service.emit(
        "group.create", identity.user_id, target_id=grp.id, target_type="group",
        request_id=request_id,
        details={
            "department_id": department_id,
            "name": name,
            "display_name": display_name,
            "description": description,
        },
    )
    return _grp_response(grp)


async def update_group(
    db: AsyncSession, identity, group_id: str,
    display_name: str | None, description: str | None, request_id=None,
) -> GroupResponse:
    _require_admin(identity)
    repo = GroupRepository(db)
    grp = await repo.get(group_id)
    if grp is None or not grp.is_active:
        raise NotFoundError(error_code="GROUP_NOT_FOUND", message="Group not found")
    await repo.update(grp, display_name=display_name, description=description)
    await db.commit()
    audit_service.emit(
        "group.update", identity.user_id, target_id=group_id, target_type="group",
        request_id=request_id,
        details={
            "group_name": grp.name,
            "changes": {k: v for k, v in {"display_name": display_name, "description": description}.items() if v is not None},
        },
    )
    return _grp_response(grp)


async def delete_group(db: AsyncSession, identity, group_id: str, request_id=None) -> None:
    _require_admin(identity)
    repo = GroupRepository(db)
    grp = await repo.get(group_id)
    if grp is None or not grp.is_active:
        raise NotFoundError(error_code="GROUP_NOT_FOUND", message="Group not found")
    await repo.deactivate(grp)
    await db.commit()
    audit_service.emit(
        "group.delete", identity.user_id, target_id=group_id, target_type="group",
        request_id=request_id,
        details={"group_name": grp.name, "display_name": grp.display_name},
    )


# ── Membership ────────────────────────────────────────────────────────────────

async def list_members(db: AsyncSession, identity, group_id: str, request_id=None) -> list[MemberResponse]:
    _require_admin(identity)
    repo = GroupRepository(db)
    grp = await repo.get(group_id)
    if grp is None or not grp.is_active:
        raise NotFoundError(error_code="GROUP_NOT_FOUND", message="Group not found")
    user_repo = UserRepository(db)
    members = await repo.list_members(group_id)
    result = []
    for m in members:
        user = await user_repo.get_by_id(m.user_id)
        if user:
            result.append(MemberResponse(user_id=user.id, username=user.username, added_at=m.added_at))
    return result


async def add_member(db: AsyncSession, identity, group_id: str, user_id: str, request_id=None) -> MemberResponse:
    if identity.platform_role not in (PlatformRole.ACCOUNT_ADMIN, PlatformRole.DEPARTMENT_ADMIN):
        raise AuthorizationError(error_code="ROLE_REQUIRED", message="Admin role required")

    repo = GroupRepository(db)
    grp = await repo.get(group_id)
    if grp is None or not grp.is_active:
        raise NotFoundError(error_code="GROUP_NOT_FOUND", message="Group not found")

    user_repo = UserRepository(db)
    user = await user_repo.get_by_id(user_id)
    if user is None:
        raise NotFoundError(error_code="USER_NOT_FOUND", message="User not found")

    if user.department_id != grp.department_id:
        raise AuthorizationError(
            error_code="GROUP_DEPARTMENT_MISMATCH",
            message=(
                f"User '{user_id}' is in a different department from group '{grp.name}'"
            ),
        )

    if identity.platform_role == PlatformRole.DEPARTMENT_ADMIN:
        if identity.department_id != grp.department_id:
            raise AuthorizationError(
                error_code="DEPARTMENT_ACCESS_DENIED",
                message="department_admin can only manage groups in their own department",
            )

    if await repo.get_membership(group_id, user_id):
        raise ConflictError(error_code="ALREADY_GROUP_MEMBER", message="User is already a member of this group")

    m = await repo.add_member(group_id, user_id, added_by=identity.user_id)
    await db.commit()
    _invalidate_identity_cache(user_id)
    audit_service.emit(
        "group.member_add", identity.user_id, target_id=group_id, target_type="group",
        details={
            "group_name": grp.name,
            "user_id": user_id,
            "target_username": user.username,
            "target_department_id": user.department_id,
        },
        request_id=request_id,
    )
    return MemberResponse(user_id=user.id, username=user.username, added_at=m.added_at)


async def remove_member(db: AsyncSession, identity, group_id: str, user_id: str, request_id=None) -> None:
    if identity.platform_role not in (PlatformRole.ACCOUNT_ADMIN, PlatformRole.DEPARTMENT_ADMIN):
        raise AuthorizationError(error_code="ROLE_REQUIRED", message="Admin role required")

    repo = GroupRepository(db)
    grp = await repo.get(group_id)
    if grp is None or not grp.is_active:
        raise NotFoundError(error_code="GROUP_NOT_FOUND", message="Group not found")

    m = await repo.get_membership(group_id, user_id)
    if m is None:
        raise NotFoundError(error_code="MEMBER_NOT_FOUND", message="User is not a member of this group")

    if identity.platform_role == PlatformRole.DEPARTMENT_ADMIN:
        if identity.department_id != grp.department_id:
            raise AuthorizationError(
                error_code="DEPARTMENT_ACCESS_DENIED",
                message="department_admin can only manage groups in their own department",
            )

    await repo.remove_member(m)
    await db.commit()
    _invalidate_identity_cache(user_id)
    audit_service.emit(
        "group.member_remove", identity.user_id, target_id=group_id, target_type="group",
        details={"group_name": grp.name, "user_id": user_id},
        request_id=request_id,
    )


async def list_user_groups(db: AsyncSession, identity, user_id: str, request_id=None) -> list[UserGroupsResponse]:
    # ── Cross-department info-disclosure guard ──────────────────────────────
    # `GET /users/{user_id}/groups` доступен любому залогиненному (свои),
    # department_admin'у (юзер своего отдела) и account_admin (любой). Без
    # dept-isolation department_admin'у dept_a достаточно пересчитать
    # `usr_*` id'шки и прочитать memberships юзеров из чужих отделов —
    # лик имён групп `prod_access`/`security_team`/etc. Зеркало
    # `user_service.list_users_by_department`.
    if identity.platform_role == PlatformRole.ACCOUNT_ADMIN:
        # account_admin — cross-department by design.
        pass
    elif identity.platform_role == PlatformRole.DEPARTMENT_ADMIN:
        # department_admin — только юзеры своего отдела. Self-inspection
        # всегда разрешён (edge case: admin сам department-scoped, смотрит свою строку).
        if identity.user_id != user_id:
            user_repo = UserRepository(db)
            target = await user_repo.get_by_id(user_id)
            if target is None:
                raise NotFoundError(
                    error_code="USER_NOT_FOUND",
                    message="User not found",
                )
            if target.department_id != identity.department_id:
                raise AuthorizationError(
                    error_code="DEPARTMENT_ACCESS_DENIED",
                    message=(
                        "department_admin can only view group memberships "
                        "for users in their own department"
                    ),
                )
    else:
        # Обычный юзер видит только свои группы.
        if identity.user_id != user_id:
            raise AuthorizationError(error_code="ROLE_REQUIRED", message="Cannot view other user's groups")

    repo = GroupRepository(db)
    memberships = await repo.list_user_groups(user_id)
    result = []
    for m in memberships:
        grp = await repo.get(m.group_id)
        if grp and grp.is_active:
            result.append(UserGroupsResponse(
                group_id=grp.id,
                group_name=grp.name,
                display_name=grp.display_name,
                added_at=m.added_at,
            ))
    return result


# ── Group service access ──────────────────────────────────────────────────────

async def list_group_services(
    db: AsyncSession, identity, group_id: str, request_id=None
) -> list[GroupServiceAccessResponse]:
    _require_admin(identity)
    repo = GroupRepository(db)
    grp = await repo.get(group_id)
    if grp is None or not grp.is_active:
        raise NotFoundError(error_code="GROUP_NOT_FOUND", message="Group not found")
    access_list = await repo.list_service_access(group_id)
    return [GroupServiceAccessResponse(
        service_name=a.service_name,
        is_active=a.is_active,
        granted_at=a.granted_at,
        granted_by=a.granted_by,
    ) for a in access_list]


async def grant_service_to_group(
    db: AsyncSession, identity, group_id: str, service_name: str, request_id=None
) -> GroupServiceAccessResponse:
    _require_admin(identity)
    repo = GroupRepository(db)
    grp = await repo.get(group_id)
    if grp is None or not grp.is_active:
        raise NotFoundError(error_code="GROUP_NOT_FOUND", message="Group not found")

    svc_repo = ServiceRepository(db)
    if not await svc_repo.exists(service_name):
        raise NotFoundError(error_code="SERVICE_NOT_FOUND", message=f"Service '{service_name}' not found")

    existing = await repo.get_service_access(group_id, service_name)
    if existing and existing.is_active:
        raise ConflictError(error_code="GROUP_SERVICE_ALREADY_GRANTED",
                            message=f"Group already has access to '{service_name}'")
    if existing:
        existing.is_active = True
        await db.flush()
        obj = existing
    else:
        obj = await repo.grant_service(group_id, service_name, granted_by=identity.user_id)
    await db.commit()
    audit_service.emit(
        "group.service_grant", identity.user_id, target_id=group_id,
        details={
            "group_name": grp.name,
            "service_name": service_name,
            "reactivated": bool(existing and existing is obj),
        },
        request_id=request_id,
    )
    return GroupServiceAccessResponse(
        service_name=obj.service_name, is_active=obj.is_active,
        granted_at=obj.granted_at, granted_by=obj.granted_by,
    )


async def revoke_service_from_group(
    db: AsyncSession, identity, group_id: str, service_name: str, request_id=None
) -> None:
    _require_admin(identity)
    repo = GroupRepository(db)
    grp = await repo.get(group_id)
    if grp is None or not grp.is_active:
        raise NotFoundError(error_code="GROUP_NOT_FOUND", message="Group not found")
    access = await repo.get_service_access(group_id, service_name)
    if access is None or not access.is_active:
        raise NotFoundError(error_code="GROUP_SERVICE_NOT_FOUND",
                            message=f"Group does not have access to '{service_name}'")
    await repo.revoke_service(access)
    await db.commit()
    audit_service.emit(
        "group.service_revoke", identity.user_id, target_id=group_id,
        details={"group_name": grp.name, "service_name": service_name},
        request_id=request_id,
    )


# ── Group service roles ───────────────────────────────────────────────────────

async def list_group_roles(
    db: AsyncSession, identity, group_id: str, request_id=None
) -> list[GroupRoleResponse]:
    _require_admin(identity)
    repo = GroupRepository(db)
    grp = await repo.get(group_id)
    if grp is None or not grp.is_active:
        raise NotFoundError(error_code="GROUP_NOT_FOUND", message="Group not found")
    rows = await repo.list_roles(group_id)
    by_service: dict[str, list[str]] = {}
    for r in rows:
        by_service.setdefault(r.service_name, []).append(r.role)
    return [GroupRoleResponse(service_name=svc, roles=roles) for svc, roles in by_service.items()]


async def assign_group_roles(
    db: AsyncSession, identity, group_id: str, service_name: str,
    roles: list[str], request_id=None,
) -> GroupRoleResponse:
    _require_admin(identity)
    repo = GroupRepository(db)
    grp = await repo.get(group_id)
    if grp is None or not grp.is_active:
        raise NotFoundError(error_code="GROUP_NOT_FOUND", message="Group not found")

    svc_repo = ServiceRepository(db)
    if not await svc_repo.exists(service_name):
        raise NotFoundError(error_code="SERVICE_NOT_FOUND", message=f"Service '{service_name}' not found")

    role_def_repo = ServiceRoleDefinitionRepository(db)
    for role in roles:
        if not await role_def_repo.exists(grp.department_id, service_name, role):
            from src.core.exceptions import DomainValidationError
            raise DomainValidationError(
                error_code="INVALID_SERVICE_ROLE",
                message=(
                    f"Role '{role}' is not defined for service '{service_name}' "
                    f"in department '{grp.department_id}'"
                ),
            )

    members_before = await repo.list_members(group_id)
    await repo.set_roles(group_id, service_name, roles, assigned_by=identity.user_id)
    await db.commit()
    for m in members_before:
        _invalidate_identity_cache(m.user_id)
    audit_service.emit(
        "group.roles_assign", identity.user_id, target_id=group_id,
        details={
            "group_name": grp.name,
            "service_name": service_name,
            "roles": list(roles),
        },
        request_id=request_id,
    )
    return GroupRoleResponse(service_name=service_name, roles=roles)


async def revoke_group_roles(
    db: AsyncSession, identity, group_id: str, service_name: str, request_id=None
) -> None:
    _require_admin(identity)
    repo = GroupRepository(db)
    grp = await repo.get(group_id)
    if grp is None or not grp.is_active:
        raise NotFoundError(error_code="GROUP_NOT_FOUND", message="Group not found")
    members_before = await repo.list_members(group_id)
    await repo.clear_roles_for_service(group_id, service_name)
    await db.commit()
    for m in members_before:
        _invalidate_identity_cache(m.user_id)
    audit_service.emit(
        "group.roles_revoke", identity.user_id, target_id=group_id,
        details={"group_name": grp.name, "service_name": service_name},
        request_id=request_id,
    )
