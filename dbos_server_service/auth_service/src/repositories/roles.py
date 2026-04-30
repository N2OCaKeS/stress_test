"""Role assignment repository."""

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.user_service_role import UserServiceRole
from src.utils.ids import _new_id


class RoleRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def get_roles_by_service(self, user_id: str, service_name: str) -> list[str]:
        rows = await self._db.scalars(
            select(UserServiceRole.role).where(
                UserServiceRole.user_id == user_id,
                UserServiceRole.service_name == service_name,
                UserServiceRole.is_active.is_(True),
            )
        )
        return list(rows)

    async def get_all_roles(self, user_id: str) -> dict[str, list[str]]:
        rows = await self._db.scalars(
            select(UserServiceRole).where(
                UserServiceRole.user_id == user_id,
                UserServiceRole.is_active.is_(True),
            )
        )
        result: dict[str, list[str]] = {}
        for row in rows:
            result.setdefault(row.service_name, []).append(row.role)
        return result

    async def set_roles(
        self,
        user_id: str,
        service_name: str,
        roles: list[str],
        assigned_by: str | None = None,
    ) -> None:
        """Replace all roles for (user, service) with the given list."""
        await self._db.execute(
            delete(UserServiceRole).where(
                UserServiceRole.user_id == user_id,
                UserServiceRole.service_name == service_name,
            )
        )
        for role in roles:
            self._db.add(
                UserServiceRole(
                    id=_new_id("usr_"),
                    user_id=user_id,
                    service_name=service_name,
                    role=role,
                    assigned_by=assigned_by,
                )
            )
        await self._db.flush()

    async def deactivate_service_roles(self, user_id: str, service_name: str) -> None:
        rows = await self._db.scalars(
            select(UserServiceRole).where(
                UserServiceRole.user_id == user_id,
                UserServiceRole.service_name == service_name,
            )
        )
        for row in rows:
            row.is_active = False
        await self._db.flush()

    async def deactivate_all_for_service(self, service_name: str) -> None:
        rows = await self._db.scalars(
            select(UserServiceRole).where(UserServiceRole.service_name == service_name)
        )
        for row in rows:
            row.is_active = False
        await self._db.flush()

    async def deactivate_by_role_name(self, service_name: str, role_name: str) -> None:
        rows = await self._db.scalars(
            select(UserServiceRole).where(
                UserServiceRole.service_name == service_name,
                UserServiceRole.role == role_name,
            )
        )
        for row in rows:
            row.is_active = False
        await self._db.flush()

    async def bulk_assign(
        self, user_ids: list[str], service_name: str, role_name: str, assigned_by: str | None
    ) -> None:
        existing_rows = await self._db.scalars(
            select(UserServiceRole).where(
                UserServiceRole.user_id.in_(user_ids),
                UserServiceRole.service_name == service_name,
                UserServiceRole.role == role_name,
            )
        )
        existing_map = {r.user_id: r for r in existing_rows}
        for user_id in user_ids:
            if user_id in existing_map:
                existing_map[user_id].is_active = True
            else:
                self._db.add(UserServiceRole(
                    id=_new_id("usr_"),
                    user_id=user_id,
                    service_name=service_name,
                    role=role_name,
                    assigned_by=assigned_by,
                ))
        await self._db.flush()

    async def bulk_revoke(self, user_ids: list[str], service_name: str, role_name: str) -> None:
        await self._db.execute(
            delete(UserServiceRole).where(
                UserServiceRole.user_id.in_(user_ids),
                UserServiceRole.service_name == service_name,
                UserServiceRole.role == role_name,
            )
        )
        await self._db.flush()
