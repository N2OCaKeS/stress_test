"""Бизнес-логика отделов: CRUD departments + grant/revoke access к сервисам."""

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import ConflictError, NotFoundError
from src.repositories.bot_roles import BotRoleRepository
from src.repositories.departments import DepartmentRepository
from src.repositories.groups import GroupRepository
from src.repositories.roles import RoleRepository
from src.repositories.service_role_definitions import ServiceRoleDefinitionRepository
from src.repositories.services import ServiceRepository
from src.schemas.departments import DepartmentResponse, ServiceAccessResponse
from src.services import audit_service
from src.services._cache_invalidation import invalidate_identity_cache as _invalidate_identity_cache
from src.utils.time import utcnow


async def create_department(
    db: AsyncSession,
    actor_id: str,
    name: str,
    display_name: str,
    request_id: str | None = None,
) -> DepartmentResponse:
    """Создать отдел. Уникальность по `name`."""
    repo = DepartmentRepository(db)
    if await repo.get_by_name(name):
        raise ConflictError(error_code="DEPARTMENT_ALREADY_EXISTS", message=f"Department '{name}' already exists")

    dept = await repo.create(name, display_name)
    await db.commit()
    audit_service.emit(
        "department.create", actor_id, target_id=dept.id, target_type="department",
        request_id=request_id,
        details={"name": name, "display_name": display_name},
    )
    return DepartmentResponse(department_id=dept.id, name=dept.name, display_name=dept.display_name, is_active=dept.is_active, created_at=dept.created_at)


async def list_departments(
    db: AsyncSession,
    actor_id: str | None = None,
    request_id: str | None = None,
) -> list[DepartmentResponse]:
    """Все отделы."""
    repo = DepartmentRepository(db)
    result = [
        DepartmentResponse(department_id=d.id, name=d.name, display_name=d.display_name, is_active=d.is_active, created_at=d.created_at)
        for d in await repo.list_all()
    ]
    audit_service.emit(
        "department.list", actor_id, status="success", allowed=True,
        request_id=request_id,
        details={"count": len(result)},
    )
    return result


async def grant_service_access(
    db: AsyncSession,
    actor_id: str,
    department_id: str,
    service_name: str,
    request_id: str | None = None,
) -> ServiceAccessResponse:
    """Выдать отделу access к сервису + засеять system-роль `admin` в новом scope."""
    dept_repo = DepartmentRepository(db)
    svc_repo = ServiceRepository(db)

    dept = await dept_repo.get_by_id(department_id)
    if dept is None:
        raise NotFoundError(error_code="DEPARTMENT_NOT_FOUND", message="Department not found")

    svc = await svc_repo.get(service_name)
    if svc is None or not svc.is_active:
        raise NotFoundError(error_code="SERVICE_NOT_FOUND", message=f"Service '{service_name}' not found")

    existing = await dept_repo.get_access(department_id, service_name)
    if existing and existing.is_active:
        raise ConflictError(error_code="SERVICE_ALREADY_GRANTED", message="Department already has access to this service")

    # Считаем `reactivated` ДО мутации `is_active=True`. Иначе SOC не
    # отличит первой выдачи от реактивации revoked access (severity
    # `department.service_grant` = CRITICAL).
    reactivated = bool(existing and not existing.is_active)

    if existing and not existing.is_active:
        # Реактивация — это эффективно новый grant: обновляем `granted_at` и
        # `granted_by` на текущего актора, иначе list-эндпоинты показывают
        # автора первой выдачи, а ответственным за актуальный доступ
        # числится кто-то другой.
        existing.is_active = True
        existing.revoked_at = None
        existing.revoked_by = None
        existing.granted_at = utcnow()
        existing.granted_by = actor_id
        await db.flush()
    else:
        await dept_repo.grant_access(department_id, service_name, granted_by=actor_id)

    # Засеять (или реактивировать) системную роль `admin` для пары (dept, service).
    role_def_repo = ServiceRoleDefinitionRepository(db)
    await role_def_repo.seed_system_admin(department_id, service_name, actor_id)

    await db.commit()
    audit_service.emit(
        "department.service_grant", actor_id, target_id=department_id, target_type="department",
        details={
            "department_name": dept.display_name,
            "service_name": service_name,
            "reactivated": reactivated,
        },
        request_id=request_id,
    )
    return ServiceAccessResponse(department_id=department_id, service_name=service_name, enabled=True)


async def revoke_service_access(
    db: AsyncSession,
    actor_id: str,
    department_id: str,
    service_name: str,
    request_id: str | None = None,
) -> None:
    """Отозвать access отдела + каскадно деактивировать все зависящие роли."""
    dept_repo = DepartmentRepository(db)
    role_repo = RoleRepository(db)

    access = await dept_repo.get_access(department_id, service_name)
    if access is None or not access.is_active:
        raise NotFoundError(error_code="SERVICE_NOT_FOUND", message="Service access not found")

    await dept_repo.revoke_access(access, revoked_by=actor_id)

    # Каскадно сносим всё, что зависело от пары (dept, service): role
    # definitions (включая системный `admin`), user→role assignments, и
    # group→role bindings для групп этого отдела.
    role_def_repo = ServiceRoleDefinitionRepository(db)
    await role_def_repo.deactivate_all_for_dept_service(department_id, service_name)
    affected_direct_user_ids = await role_repo.deactivate_all_in_dept_for_service(
        department_id, service_name,
    )
    group_repo = GroupRepository(db)
    affected_group_ids = await group_repo.deactivate_all_dept_service_roles(
        department_id, service_name,
    )
    # Симметрично снимаем `GroupServiceAccess`: без этого
    # `list_active_services_by_groups` продолжает возвращать `service_name`,
    # и `_merge_permissions` (`dept ∪ group`) добавляет revoked-сервис обратно
    # в `allowed_services` юзера через group-канал — downstream-сервисы
    # пускали бы по stale scope.
    affected_access_group_ids = await group_repo.deactivate_all_dept_service_access(
        department_id, service_name,
    )
    bot_role_repo = BotRoleRepository(db)
    affected_direct_bot_ids = await bot_role_repo.deactivate_all_in_dept_for_service(
        department_id, service_name,
    )

    # Собираем юзеров, которым нужен cache-invalidation: прямые носители роли
    # + члены групп, у которых сняли group→role binding ИЛИ group→service-access.
    # Без сброса они до TTL=5s могли бы продолжать обращаться к сервису, у
    # которого отдел уже не имеет доступа — `_merge_permissions` INTERSECT-
    # инвариант нарушался.
    affected_user_ids: set[str] = set(affected_direct_user_ids)
    affected_bot_ids: set[str] = set(affected_direct_bot_ids)
    member_group_ids = set(affected_group_ids) | set(affected_access_group_ids)
    if member_group_ids:
        affected_user_ids.update(
            await group_repo.list_member_user_ids(list(member_group_ids))
        )
        affected_bot_ids.update(
            await group_repo.list_member_bot_ids(list(member_group_ids))
        )

    await db.commit()
    for uid in affected_user_ids:
        _invalidate_identity_cache(uid)
    for bid in affected_bot_ids:
        _invalidate_identity_cache(bid)
    audit_service.emit(
        "department.service_revoke", actor_id, target_id=department_id, target_type="department",
        details={
            "service_name": service_name,
            "cascade_deactivated_roles": True,
            "affected_user_count": len(affected_user_ids),
            "affected_bot_count": len(affected_bot_ids),
        },
        request_id=request_id,
    )
