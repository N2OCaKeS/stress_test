"""DAO для `ServiceRoleDefinition` (scope `(dept, service, role_name)`).

Системная роль `admin` сеется через `seed_system_admin` при grant'е dept-access
к сервису — `is_system=True` защищает её от модификации/удаления через API.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.service_role_definition import ServiceRoleDefinition
from src.utils.ids import service_role_def_id


SYSTEM_ADMIN_ROLE_NAME = "admin"


class ServiceRoleDefinitionRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def get(
        self, department_id: str, service_name: str, role_name: str
    ) -> ServiceRoleDefinition | None:
        return await self._db.scalar(
            select(ServiceRoleDefinition).where(
                ServiceRoleDefinition.department_id == department_id,
                ServiceRoleDefinition.service_name == service_name,
                ServiceRoleDefinition.role_name == role_name,
                ServiceRoleDefinition.is_active.is_(True),
            )
        )

    async def list_active(
        self, department_id: str, service_name: str
    ) -> list[ServiceRoleDefinition]:
        result = await self._db.scalars(
            select(ServiceRoleDefinition).where(
                ServiceRoleDefinition.department_id == department_id,
                ServiceRoleDefinition.service_name == service_name,
                ServiceRoleDefinition.is_active.is_(True),
            )
        )
        return list(result)

    async def exists(
        self, department_id: str, service_name: str, role_name: str
    ) -> bool:
        return (
            await self._db.scalar(
                select(ServiceRoleDefinition.id).where(
                    ServiceRoleDefinition.department_id == department_id,
                    ServiceRoleDefinition.service_name == service_name,
                    ServiceRoleDefinition.role_name == role_name,
                    ServiceRoleDefinition.is_active.is_(True),
                )
            )
            is not None
        )

    async def create(
        self,
        department_id: str,
        service_name: str,
        role_name: str,
        description: str | None,
        created_by: str | None,
        is_system: bool = False,
    ) -> ServiceRoleDefinition:
        obj = ServiceRoleDefinition(
            id=service_role_def_id(),
            department_id=department_id,
            service_name=service_name,
            role_name=role_name,
            description=description,
            is_system=is_system,
            created_by=created_by,
        )
        self._db.add(obj)
        await self._db.flush()
        return obj

    async def seed_system_admin(
        self, department_id: str, service_name: str, actor_id: str | None
    ) -> ServiceRoleDefinition:
        """Create the immutable `admin` role for (department, service).

        Reactivates an existing soft-deleted system row if present, otherwise
        inserts a fresh one. Idempotent for already-active rows.
        """
        existing = await self._db.scalar(
            select(ServiceRoleDefinition).where(
                ServiceRoleDefinition.department_id == department_id,
                ServiceRoleDefinition.service_name == service_name,
                ServiceRoleDefinition.role_name == SYSTEM_ADMIN_ROLE_NAME,
            )
        )
        if existing is not None:
            if not existing.is_active:
                existing.is_active = True
            existing.is_system = True
            await self._db.flush()
            return existing

        return await self.create(
            department_id=department_id,
            service_name=service_name,
            role_name=SYSTEM_ADMIN_ROLE_NAME,
            description="Full administrative access to the service",
            created_by=actor_id,
            is_system=True,
        )

    async def update(
        self,
        role_def: ServiceRoleDefinition,
        description: str | None = None,
    ) -> None:
        if description is not None:
            role_def.description = description
        await self._db.flush()

    async def deactivate(self, role_def: ServiceRoleDefinition) -> None:
        role_def.is_active = False
        await self._db.flush()

    async def deactivate_all_for_service(self, service_name: str) -> None:
        """Soft-delete every role definition for a service across all departments."""
        rows = await self._db.scalars(
            select(ServiceRoleDefinition).where(
                ServiceRoleDefinition.service_name == service_name
            )
        )
        for row in rows:
            row.is_active = False
        await self._db.flush()

    async def deactivate_all_for_dept_service(
        self, department_id: str, service_name: str
    ) -> None:
        """Soft-delete every role definition for a (department, service) pair."""
        rows = await self._db.scalars(
            select(ServiceRoleDefinition).where(
                ServiceRoleDefinition.department_id == department_id,
                ServiceRoleDefinition.service_name == service_name,
            )
        )
        for row in rows:
            row.is_active = False
        await self._db.flush()
