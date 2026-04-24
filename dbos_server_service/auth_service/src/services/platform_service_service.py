"""Platform service registry workflows."""

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import ConflictError, NotFoundError
from src.repositories.departments import DepartmentRepository
from src.repositories.roles import RoleRepository
from src.repositories.service_role_definitions import ServiceRoleDefinitionRepository
from src.repositories.services import ServiceRepository
from src.schemas.services import ServiceResponse
from src.services import audit_service


async def create_service(
    db: AsyncSession,
    actor_id: str,
    service_name: str,
    display_name: str,
    description: str | None,
    request_id: str | None = None,
) -> ServiceResponse:
    repo = ServiceRepository(db)
    if await repo.exists(service_name):
        raise ConflictError(error_code="SERVICE_ALREADY_EXISTS", message=f"Service '{service_name}' already exists")

    svc = await repo.create(service_name, display_name, description)
    role_def_repo = ServiceRoleDefinitionRepository(db)
    await role_def_repo.create(
        service_name=service_name,
        role_name="admin",
        display_name="Admin",
        description="Full administrative access to the service",
        created_by=actor_id,
    )
    await db.commit()
    audit_service.emit("service.create", actor_id, target_id=service_name, target_type="service", request_id=request_id)
    return ServiceResponse(
        service_name=svc.service_name,
        display_name=svc.display_name,
        description=svc.description,
        is_active=svc.is_active,
        created_at=svc.created_at,
    )


async def delete_service(
    db: AsyncSession,
    actor_id: str,
    service_name: str,
    request_id: str | None = None,
) -> None:
    svc_repo = ServiceRepository(db)
    dept_repo = DepartmentRepository(db)
    role_repo = RoleRepository(db)

    svc = await svc_repo.get(service_name)
    if svc is None or not svc.is_active:
        raise NotFoundError(error_code="SERVICE_NOT_FOUND", message=f"Service '{service_name}' not found")

    for dept in await dept_repo.list_all():
        access = await dept_repo.get_access(dept.id, service_name)
        if access and access.is_active:
            await dept_repo.revoke_access(access, revoked_by=actor_id)

    await role_repo.deactivate_all_for_service(service_name)
    role_def_repo = ServiceRoleDefinitionRepository(db)
    await role_def_repo.deactivate_all_for_service(service_name)
    await svc_repo.deactivate(svc)
    await db.commit()
    audit_service.emit("service.delete", actor_id, target_id=service_name, target_type="service", request_id=request_id)


async def list_services(
    db: AsyncSession,
    actor_id: str | None = None,
    request_id: str | None = None,
) -> list[ServiceResponse]:
    repo = ServiceRepository(db)
    result = [
        ServiceResponse(
            service_name=s.service_name,
            display_name=s.display_name,
            description=s.description,
            is_active=s.is_active,
            created_at=s.created_at,
        )
        for s in await repo.list_active()
    ]
    audit_service.emit("service.list", actor_id, status="success", allowed=True, request_id=request_id)
    return result
