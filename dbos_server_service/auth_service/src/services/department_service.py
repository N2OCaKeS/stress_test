"""Department management and service access workflows."""

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import ConflictError, NotFoundError
from src.repositories.departments import DepartmentRepository
from src.repositories.roles import RoleRepository
from src.repositories.services import ServiceRepository
from src.schemas.departments import DepartmentResponse, ServiceAccessResponse
from src.services import audit_service


async def create_department(
    db: AsyncSession,
    actor_id: str,
    name: str,
    display_name: str,
    request_id: str | None = None,
) -> DepartmentResponse:
    repo = DepartmentRepository(db)
    if await repo.get_by_name(name):
        raise ConflictError(error_code="DEPARTMENT_ALREADY_EXISTS", message=f"Department '{name}' already exists")

    dept = await repo.create(name, display_name)
    await db.commit()
    audit_service.emit("department.create", actor_id, target_id=dept.id, target_type="department", request_id=request_id)
    return DepartmentResponse(department_id=dept.id, name=dept.name, display_name=dept.display_name, is_active=dept.is_active, created_at=dept.created_at)


async def list_departments(
    db: AsyncSession,
    actor_id: str | None = None,
    request_id: str | None = None,
) -> list[DepartmentResponse]:
    repo = DepartmentRepository(db)
    result = [
        DepartmentResponse(department_id=d.id, name=d.name, display_name=d.display_name, is_active=d.is_active, created_at=d.created_at)
        for d in await repo.list_all()
    ]
    audit_service.emit("department.list", actor_id, status="success", allowed=True, request_id=request_id)
    return result


async def grant_service_access(
    db: AsyncSession,
    actor_id: str,
    department_id: str,
    service_name: str,
    request_id: str | None = None,
) -> ServiceAccessResponse:
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

    if existing and not existing.is_active:
        existing.is_active = True
        existing.revoked_at = None
        existing.revoked_by = None
        await db.flush()
    else:
        await dept_repo.grant_access(department_id, service_name, granted_by=actor_id)

    await db.commit()
    audit_service.emit("department.service_grant", actor_id, target_id=department_id, target_type="department", details={"service_name": service_name}, request_id=request_id)
    return ServiceAccessResponse(department_id=department_id, service_name=service_name, enabled=True)


async def revoke_service_access(
    db: AsyncSession,
    actor_id: str,
    department_id: str,
    service_name: str,
    request_id: str | None = None,
) -> None:
    dept_repo = DepartmentRepository(db)
    role_repo = RoleRepository(db)

    access = await dept_repo.get_access(department_id, service_name)
    if access is None or not access.is_active:
        raise NotFoundError(error_code="SERVICE_NOT_FOUND", message="Service access not found")

    await dept_repo.revoke_access(access, revoked_by=actor_id)
    await db.commit()
    audit_service.emit("department.service_revoke", actor_id, target_id=department_id, target_type="department", details={"service_name": service_name}, request_id=request_id)
