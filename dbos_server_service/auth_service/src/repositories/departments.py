"""Department repository."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.department import Department
from src.models.department_service_access import DepartmentServiceAccess
from src.utils.ids import department_id
from src.utils.time import utcnow


class DepartmentRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def get_by_id(self, dept_id: str) -> Department | None:
        return await self._db.get(Department, dept_id)

    async def get_by_name(self, name: str) -> Department | None:
        return await self._db.scalar(select(Department).where(Department.name == name))

    async def list_all(self) -> list[Department]:
        result = await self._db.scalars(select(Department).where(Department.is_active.is_(True)))
        return list(result)

    async def create(self, name: str, display_name: str) -> Department:
        dept = Department(id=department_id(), name=name, display_name=display_name)
        self._db.add(dept)
        await self._db.flush()
        return dept

    # ── service access ────────────────────────────────────────────────────────

    async def get_access(self, dept_id: str, service_name: str) -> DepartmentServiceAccess | None:
        return await self._db.scalar(
            select(DepartmentServiceAccess).where(
                DepartmentServiceAccess.department_id == dept_id,
                DepartmentServiceAccess.service_name == service_name,
            )
        )

    async def has_active_access(self, dept_id: str, service_name: str) -> bool:
        row = await self.get_access(dept_id, service_name)
        return row is not None and row.is_active

    async def list_active_services(self, dept_id: str) -> list[str]:
        rows = await self._db.scalars(
            select(DepartmentServiceAccess.service_name).where(
                DepartmentServiceAccess.department_id == dept_id,
                DepartmentServiceAccess.is_active.is_(True),
            )
        )
        return list(rows)

    async def grant_access(
        self, dept_id: str, service_name: str, granted_by: str | None
    ) -> DepartmentServiceAccess:
        from src.utils.ids import _new_id
        access = DepartmentServiceAccess(
            id=_new_id("dsa_"),
            department_id=dept_id,
            service_name=service_name,
            is_active=True,
            granted_by=granted_by,
        )
        self._db.add(access)
        await self._db.flush()
        return access

    async def revoke_access(
        self, access: DepartmentServiceAccess, revoked_by: str | None
    ) -> None:
        access.is_active = False
        access.revoked_at = utcnow()
        access.revoked_by = revoked_by
        await self._db.flush()
