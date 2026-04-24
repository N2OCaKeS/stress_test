"""Service role definition management workflows."""

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.constants import PlatformRole
from src.core.exceptions import AuthorizationError, ConflictError, NotFoundError
from src.repositories.groups import GroupRepository
from src.repositories.roles import RoleRepository
from src.repositories.service_role_definitions import ServiceRoleDefinitionRepository
from src.repositories.services import ServiceRepository
from src.schemas.auth import IdentityContext
from src.schemas.service_roles import ServiceRoleResponse
from src.services import audit_service


def _check_can_manage(identity: IdentityContext, service_name: str) -> None:
    """account_admin or user with 'admin' role on the service."""
    if identity.platform_role == PlatformRole.ACCOUNT_ADMIN:
        return
    if "admin" in identity.service_roles.get(service_name, []):
        return
    raise AuthorizationError(
        error_code="SERVICE_ROLE_MGMT_FORBIDDEN",
        message=f"account_admin or '{service_name}' admin role required",
    )


def _to_response(obj) -> ServiceRoleResponse:
    return ServiceRoleResponse(
        id=obj.id,
        service_name=obj.service_name,
        role_name=obj.role_name,
        display_name=obj.display_name,
        description=obj.description,
        is_active=obj.is_active,
        created_at=obj.created_at,
        created_by=obj.created_by,
    )


async def list_roles(
    db: AsyncSession,
    identity: IdentityContext,
    service_name: str,
    request_id: str | None = None,
) -> list[ServiceRoleResponse]:
    _check_can_manage(identity, service_name)
    svc_repo = ServiceRepository(db)
    if not await svc_repo.exists(service_name):
        raise NotFoundError(error_code="SERVICE_NOT_FOUND", message=f"Service '{service_name}' not found")
    repo = ServiceRoleDefinitionRepository(db)
    return [_to_response(r) for r in await repo.list_active(service_name)]


async def create_role(
    db: AsyncSession,
    identity: IdentityContext,
    service_name: str,
    role_name: str,
    display_name: str,
    description: str | None,
    request_id: str | None = None,
) -> ServiceRoleResponse:
    _check_can_manage(identity, service_name)
    svc_repo = ServiceRepository(db)
    if not await svc_repo.exists(service_name):
        raise NotFoundError(error_code="SERVICE_NOT_FOUND", message=f"Service '{service_name}' not found")

    repo = ServiceRoleDefinitionRepository(db)
    if await repo.exists(service_name, role_name):
        raise ConflictError(
            error_code="SERVICE_ROLE_ALREADY_EXISTS",
            message=f"Role '{role_name}' already exists for service '{service_name}'",
        )

    obj = await repo.create(
        service_name=service_name,
        role_name=role_name,
        display_name=display_name,
        description=description,
        created_by=identity.user_id,
    )
    await db.commit()
    audit_service.emit("service_role.create", identity.user_id, target_id=service_name,
                       target_type="service_role", details={"role_name": role_name}, request_id=request_id)
    return _to_response(obj)


async def update_role(
    db: AsyncSession,
    identity: IdentityContext,
    service_name: str,
    role_name: str,
    display_name: str | None,
    description: str | None,
    request_id: str | None = None,
) -> ServiceRoleResponse:
    _check_can_manage(identity, service_name)
    repo = ServiceRoleDefinitionRepository(db)
    obj = await repo.get(service_name, role_name)
    if obj is None:
        raise NotFoundError(
            error_code="SERVICE_ROLE_NOT_FOUND",
            message=f"Role '{role_name}' not found for service '{service_name}'",
        )
    await repo.update(obj, display_name=display_name, description=description)
    await db.commit()
    audit_service.emit("service_role.update", identity.user_id, target_id=service_name,
                       target_type="service_role", details={"role_name": role_name}, request_id=request_id)
    return _to_response(obj)


async def delete_role(
    db: AsyncSession,
    identity: IdentityContext,
    service_name: str,
    role_name: str,
    request_id: str | None = None,
) -> None:
    _check_can_manage(identity, service_name)
    repo = ServiceRoleDefinitionRepository(db)
    obj = await repo.get(service_name, role_name)
    if obj is None:
        raise NotFoundError(
            error_code="SERVICE_ROLE_NOT_FOUND",
            message=f"Role '{role_name}' not found for service '{service_name}'",
        )
    await repo.deactivate(obj)
    # auto-revoke this role from all users and groups
    role_repo = RoleRepository(db)
    await role_repo.deactivate_by_role_name(service_name, role_name)
    group_repo = GroupRepository(db)
    await group_repo.deactivate_roles_by_role_name(service_name, role_name)
    await db.commit()
    audit_service.emit("service_role.delete", identity.user_id, target_id=service_name,
                       target_type="service_role", details={"role_name": role_name}, request_id=request_id)


async def bulk_assign(
    db: AsyncSession,
    identity: IdentityContext,
    service_name: str,
    role_name: str,
    user_ids: list[str],
    request_id: str | None = None,
) -> None:
    _check_can_manage(identity, service_name)
    repo = ServiceRoleDefinitionRepository(db)
    if not await repo.exists(service_name, role_name):
        raise NotFoundError(error_code="SERVICE_ROLE_NOT_FOUND",
                            message=f"Role '{role_name}' not found for service '{service_name}'")
    from src.repositories.departments import DepartmentRepository
    from src.repositories.users import UserRepository
    user_repo = UserRepository(db)
    dept_repo = DepartmentRepository(db)
    role_repo = RoleRepository(db)
    for user_id in user_ids:
        user = await user_repo.get_by_id(user_id)
        if user is None:
            raise NotFoundError(error_code="USER_NOT_FOUND", message=f"User '{user_id}' not found")
        if not await dept_repo.has_active_access(user.department_id, service_name):
            from src.core.exceptions import AuthorizationError as AE
            raise AE(error_code="SERVICE_NOT_ALLOWED_FOR_DEPARTMENT",
                     message=f"User '{user_id}' department has no access to '{service_name}'")
    await role_repo.bulk_assign(user_ids, service_name, role_name, assigned_by=identity.user_id)
    await db.commit()
    audit_service.emit("service_role.bulk_assign", identity.user_id, target_id=service_name,
                       details={"role_name": role_name, "user_ids": user_ids}, request_id=request_id)


async def bulk_revoke(
    db: AsyncSession,
    identity: IdentityContext,
    service_name: str,
    role_name: str,
    user_ids: list[str],
    request_id: str | None = None,
) -> None:
    _check_can_manage(identity, service_name)
    role_repo = RoleRepository(db)
    await role_repo.bulk_revoke(user_ids, service_name, role_name)
    await db.commit()
    audit_service.emit("service_role.bulk_revoke", identity.user_id, target_id=service_name,
                       details={"role_name": role_name, "user_ids": user_ids}, request_id=request_id)
