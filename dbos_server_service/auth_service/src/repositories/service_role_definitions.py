"""Service role definition repository."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.service_role_definition import ServiceRoleDefinition
from src.utils.ids import service_role_def_id


class ServiceRoleDefinitionRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def get(self, service_name: str, role_name: str) -> ServiceRoleDefinition | None:
        return await self._db.scalar(
            select(ServiceRoleDefinition).where(
                ServiceRoleDefinition.service_name == service_name,
                ServiceRoleDefinition.role_name == role_name,
                ServiceRoleDefinition.is_active.is_(True),
            )
        )

    async def list_active(self, service_name: str) -> list[ServiceRoleDefinition]:
        result = await self._db.scalars(
            select(ServiceRoleDefinition).where(
                ServiceRoleDefinition.service_name == service_name,
                ServiceRoleDefinition.is_active.is_(True),
            )
        )
        return list(result)

    async def exists(self, service_name: str, role_name: str) -> bool:
        return await self._db.scalar(
            select(ServiceRoleDefinition.id).where(
                ServiceRoleDefinition.service_name == service_name,
                ServiceRoleDefinition.role_name == role_name,
                ServiceRoleDefinition.is_active.is_(True),
            )
        ) is not None

    async def create(
        self,
        service_name: str,
        role_name: str,
        display_name: str,
        description: str | None,
        created_by: str | None,
    ) -> ServiceRoleDefinition:
        obj = ServiceRoleDefinition(
            id=service_role_def_id(),
            service_name=service_name,
            role_name=role_name,
            display_name=display_name,
            description=description,
            created_by=created_by,
        )
        self._db.add(obj)
        await self._db.flush()
        return obj

    async def update(
        self,
        role_def: ServiceRoleDefinition,
        display_name: str | None = None,
        description: str | None = None,
    ) -> None:
        if display_name is not None:
            role_def.display_name = display_name
        if description is not None:
            role_def.description = description
        await self._db.flush()

    async def deactivate(self, role_def: ServiceRoleDefinition) -> None:
        role_def.is_active = False
        await self._db.flush()

    async def deactivate_all_for_service(self, service_name: str) -> None:
        rows = await self._db.scalars(
            select(ServiceRoleDefinition).where(
                ServiceRoleDefinition.service_name == service_name
            )
        )
        for row in rows:
            row.is_active = False
        await self._db.flush()
