"""Бизнес-логика `ServiceRoleDefinition` (per-department scope: dept × service × role_name).

Системные роли (`is_system=True`, например `admin`) защищены от модификации
и удаления — снять их можно только сносом dept-service-access.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.constants import PlatformRole, ServiceRole
from src.core.exceptions import (
    AuthorizationError,
    ConflictError,
    DomainValidationError,
    NotFoundError,
)
from src.repositories.bot_roles import BotRoleRepository
from src.repositories.departments import DepartmentRepository
from src.repositories.groups import GroupRepository
from src.repositories.roles import RoleRepository
from src.repositories.service_role_definitions import ServiceRoleDefinitionRepository
from src.repositories.services import ServiceRepository
from src.repositories.users import UserRepository
from src.schemas.auth import IdentityContext
from src.schemas.service_roles import ServiceRoleResponse
from src.services import audit_service
from src.services._cache_invalidation import invalidate_identity_cache as _invalidate_identity_cache


def _check_can_manage(
    identity: IdentityContext, department_id: str, service_name: str
) -> None:
    """Проверка прав на управление ролями в (department_id, service_name).

    account_admin — везде. department_admin и любой носитель service-level
    `admin`-роли — только в своём отделе, причём admin-role holder ограничен
    тем сервисом, на котором у него `admin`.
    """
    if identity.platform_role == PlatformRole.ACCOUNT_ADMIN:
        return
    if identity.department_id != department_id:
        raise AuthorizationError(
            error_code="DEPARTMENT_FORBIDDEN",
            message="Cannot manage roles outside your own department",
        )
    if identity.platform_role == PlatformRole.DEPARTMENT_ADMIN:
        return
    if ServiceRole.ADMIN.value in identity.service_roles.get(service_name, []):
        return
    raise AuthorizationError(
        error_code="SERVICE_ROLE_MGMT_FORBIDDEN",
        message=(
            "account_admin, department_admin, or the service '" + service_name
            + "' admin role required"
        ),
    )


def _to_response(obj) -> ServiceRoleResponse:
    """ORM ServiceRoleDefinition → DTO."""
    return ServiceRoleResponse(
        id=obj.id,
        department_id=obj.department_id,
        service_name=obj.service_name,
        role_name=obj.role_name,
        display_name=obj.display_name,
        description=obj.description,
        is_active=obj.is_active,
        is_system=obj.is_system,
        created_at=obj.created_at,
        created_by=obj.created_by,
    )


async def _require_dept_service_access(
    db: AsyncSession, department_id: str, service_name: str
) -> None:
    """Пара (отдел, сервис) должна существовать и иметь active access."""
    dept_repo = DepartmentRepository(db)
    if await dept_repo.get_by_id(department_id) is None:
        raise NotFoundError(
            error_code="DEPARTMENT_NOT_FOUND",
            message=f"Department '{department_id}' not found",
        )
    svc_repo = ServiceRepository(db)
    if not await svc_repo.exists(service_name):
        raise NotFoundError(
            error_code="SERVICE_NOT_FOUND",
            message=f"Service '{service_name}' not found",
        )
    if not await dept_repo.has_active_access(department_id, service_name):
        raise DomainValidationError(
            error_code="SERVICE_NOT_GRANTED_FOR_DEPARTMENT",
            message=(
                f"Department '{department_id}' has no active access to "
                f"service '{service_name}'"
            ),
        )


async def list_roles(
    db: AsyncSession,
    identity: IdentityContext,
    department_id: str,
    service_name: str,
    request_id: str | None = None,
) -> list[ServiceRoleResponse]:
    """Список ролей в scope `(dept, service)`."""
    _check_can_manage(identity, department_id, service_name)
    await _require_dept_service_access(db, department_id, service_name)
    repo = ServiceRoleDefinitionRepository(db)
    return [_to_response(r) for r in await repo.list_active(department_id, service_name)]


async def create_role(
    db: AsyncSession,
    identity: IdentityContext,
    department_id: str,
    service_name: str,
    role_name: str,
    display_name: str,
    description: str | None,
    request_id: str | None = None,
) -> ServiceRoleResponse:
    """Создать новое определение роли. Уникальность по (dept, service, role_name)."""
    _check_can_manage(identity, department_id, service_name)
    await _require_dept_service_access(db, department_id, service_name)

    repo = ServiceRoleDefinitionRepository(db)
    if await repo.exists(department_id, service_name, role_name):
        raise ConflictError(
            error_code="SERVICE_ROLE_ALREADY_EXISTS",
            message=(
                f"Role '{role_name}' already exists for service '{service_name}' "
                f"in department '{department_id}'"
            ),
        )

    obj = await repo.create(
        department_id=department_id,
        service_name=service_name,
        role_name=role_name,
        display_name=display_name,
        description=description,
        created_by=identity.user_id,
    )
    await db.commit()
    audit_service.emit(
        "service_role.create",
        identity.user_id,
        target_id=service_name,
        target_type="service_role",
        details={
            "department_id": department_id,
            "service_name": service_name,
            "role_name": role_name,
            "display_name": display_name,
            "description": description,
        },
        request_id=request_id,
    )
    return _to_response(obj)


async def update_role(
    db: AsyncSession,
    identity: IdentityContext,
    department_id: str,
    service_name: str,
    role_name: str,
    display_name: str | None,
    description: str | None,
    request_id: str | None = None,
) -> ServiceRoleResponse:
    """Patch display_name/description. Системные роли (`is_system`) не трогаем."""
    _check_can_manage(identity, department_id, service_name)
    repo = ServiceRoleDefinitionRepository(db)
    obj = await repo.get(department_id, service_name, role_name)
    if obj is None:
        raise NotFoundError(
            error_code="SERVICE_ROLE_NOT_FOUND",
            message=(
                f"Role '{role_name}' not found for service '{service_name}' "
                f"in department '{department_id}'"
            ),
        )
    if obj.is_system:
        raise AuthorizationError(
            error_code="SERVICE_ROLE_SYSTEM_LOCKED",
            message=f"Role '{role_name}' is system-managed and cannot be modified",
        )
    await repo.update(obj, display_name=display_name, description=description)
    await db.commit()
    audit_service.emit(
        "service_role.update",
        identity.user_id,
        target_id=service_name,
        target_type="service_role",
        details={
            "department_id": department_id,
            "service_name": service_name,
            "role_name": role_name,
            "changes": {
                k: v
                for k, v in {
                    "display_name": display_name,
                    "description": description,
                }.items()
                if v is not None
            },
        },
        request_id=request_id,
    )
    return _to_response(obj)


async def delete_role(
    db: AsyncSession,
    identity: IdentityContext,
    department_id: str,
    service_name: str,
    role_name: str,
    request_id: str | None = None,
) -> None:
    """Удалить роль (`is_system=True` — не трогаем). Каскадно отзывает её у юзеров/групп/ботов."""
    _check_can_manage(identity, department_id, service_name)
    repo = ServiceRoleDefinitionRepository(db)
    obj = await repo.get(department_id, service_name, role_name)
    if obj is None:
        raise NotFoundError(
            error_code="SERVICE_ROLE_NOT_FOUND",
            message=(
                f"Role '{role_name}' not found for service '{service_name}' "
                f"in department '{department_id}'"
            ),
        )
    if obj.is_system:
        raise AuthorizationError(
            error_code="SERVICE_ROLE_SYSTEM_LOCKED",
            message=f"Role '{role_name}' is system-managed and cannot be deleted",
        )
    await repo.deactivate(obj)
    role_repo = RoleRepository(db)
    affected_user_ids = await role_repo.deactivate_by_role_name_in_dept(
        department_id, service_name, role_name
    )
    group_repo = GroupRepository(db)
    affected_group_ids = await group_repo.deactivate_roles_by_role_name_in_dept(
        department_id, service_name, role_name
    )
    bot_role_repo = BotRoleRepository(db)
    affected_bot_ids = await bot_role_repo.deactivate_by_role_name_in_dept(
        department_id, service_name, role_name
    )
    # Юзеры-члены затронутых групп тоже теряют роль через group-binding —
    # их identity-cache надо сбросить так же, как у прямых носителей.
    group_member_ids = await group_repo.list_member_user_ids(affected_group_ids)
    # То же для ботов: бот-член группы с group→role binding теряет роль; плюс
    # боты с прямым (bot, role) binding. Identity-кэш на bot_id ключи —
    # сбрасываем оптом, чтобы revoke вступил в силу до истечения TTL.
    group_bot_ids = await group_repo.list_member_bot_ids(affected_group_ids)
    await db.commit()
    cache_targets = set(affected_user_ids) | set(group_member_ids)
    for uid in cache_targets:
        _invalidate_identity_cache(uid)
    bot_cache_targets = set(affected_bot_ids) | set(group_bot_ids)
    for bid in bot_cache_targets:
        _invalidate_identity_cache(bid)
    audit_service.emit(
        "service_role.delete",
        identity.user_id,
        target_id=service_name,
        target_type="service_role",
        details={
            "department_id": department_id,
            "service_name": service_name,
            "role_name": role_name,
            "auto_revoked_from_users": True,
            "auto_revoked_from_groups": True,
            "auto_revoked_from_bots": True,
        },
        request_id=request_id,
    )


async def bulk_assign(
    db: AsyncSession,
    identity: IdentityContext,
    department_id: str,
    service_name: str,
    role_name: str,
    user_ids: list[str],
    request_id: str | None = None,
) -> None:
    """Bulk-выдать роль списку юзеров. Юзеры обязательно из этого отдела."""
    _check_can_manage(identity, department_id, service_name)
    repo = ServiceRoleDefinitionRepository(db)
    if not await repo.exists(department_id, service_name, role_name):
        raise NotFoundError(
            error_code="SERVICE_ROLE_NOT_FOUND",
            message=(
                f"Role '{role_name}' not found for service '{service_name}' "
                f"in department '{department_id}'"
            ),
        )

    user_repo = UserRepository(db)
    role_repo = RoleRepository(db)
    # Один SELECT по списку вместо N×get_by_id.
    users = await user_repo.list_by_ids(user_ids)
    by_id = {u.id: u for u in users}
    for user_id in user_ids:
        user = by_id.get(user_id)
        if user is None:
            raise NotFoundError(
                error_code="USER_NOT_FOUND", message=f"User '{user_id}' not found"
            )
        if user.department_id != department_id:
            raise AuthorizationError(
                error_code="USER_DEPARTMENT_MISMATCH",
                message=(
                    f"User '{user_id}' belongs to a different department "
                    f"and cannot be granted roles in '{department_id}'"
                ),
            )
    await role_repo.bulk_assign(
        user_ids, service_name, role_name, assigned_by=identity.user_id
    )
    await db.commit()
    for user_id in user_ids:
        _invalidate_identity_cache(user_id)
    audit_service.emit(
        "service_role.bulk_assign",
        identity.user_id,
        target_id=service_name,
        details={
            "department_id": department_id,
            "service_name": service_name,
            "role_name": role_name,
            "user_ids": list(user_ids),
            "user_count": len(user_ids),
        },
        request_id=request_id,
    )


async def bulk_revoke(
    db: AsyncSession,
    identity: IdentityContext,
    department_id: str,
    service_name: str,
    role_name: str,
    user_ids: list[str],
    request_id: str | None = None,
) -> None:
    """Bulk-снять роль со списка юзеров. Юзеры обязательно из этого отдела."""
    _check_can_manage(identity, department_id, service_name)
    user_repo = UserRepository(db)
    # Один SELECT по списку вместо N×get_by_id.
    users = await user_repo.list_by_ids(user_ids)
    by_id = {u.id: u for u in users}
    for user_id in user_ids:
        user = by_id.get(user_id)
        if user is None:
            raise NotFoundError(
                error_code="USER_NOT_FOUND", message=f"User '{user_id}' not found"
            )
        if user.department_id != department_id:
            raise AuthorizationError(
                error_code="USER_DEPARTMENT_MISMATCH",
                message=(
                    f"User '{user_id}' belongs to a different department "
                    f"and cannot be revoked from roles in '{department_id}'"
                ),
            )
    role_repo = RoleRepository(db)
    await role_repo.bulk_revoke(user_ids, service_name, role_name)
    await db.commit()
    for user_id in user_ids:
        _invalidate_identity_cache(user_id)
    audit_service.emit(
        "service_role.bulk_revoke",
        identity.user_id,
        target_id=service_name,
        details={
            "department_id": department_id,
            "service_name": service_name,
            "role_name": role_name,
            "user_ids": list(user_ids),
            "user_count": len(user_ids),
        },
        request_id=request_id,
    )
