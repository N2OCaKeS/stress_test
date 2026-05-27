"""DAO для `UserGroup` + membership + group-service-access/role."""

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.group_service_access import GroupServiceAccess
from src.models.group_service_role import GroupServiceRole
from src.models.user_group import UserGroup
from src.models.user_group_membership import UserGroupMembership
from src.utils.ids import group_id, group_membership_id, group_service_access_id, group_service_role_id


class GroupRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    # ── Group CRUD ────────────────────────────────────────────────────────────

    async def get(self, group_id_: str) -> UserGroup | None:
        return await self._db.get(UserGroup, group_id_)

    async def get_by_name(self, department_id: str, name: str) -> UserGroup | None:
        return await self._db.scalar(
            select(UserGroup).where(
                UserGroup.department_id == department_id,
                UserGroup.name == name,
                UserGroup.is_active.is_(True),
            )
        )

    async def list_active(
        self, limit: int | None = None, offset: int = 0
    ) -> list[UserGroup]:
        stmt = (
            select(UserGroup)
            .where(UserGroup.is_active.is_(True))
            .order_by(UserGroup.created_at, UserGroup.id)
        )
        if limit is not None:
            stmt = stmt.limit(limit).offset(offset)
        result = await self._db.scalars(stmt)
        return list(result)

    async def count_active(self) -> int:
        return await self._db.scalar(
            select(func.count())
            .select_from(UserGroup)
            .where(UserGroup.is_active.is_(True))
        ) or 0

    async def list_active_by_ids(self, group_ids: list[str]) -> list[UserGroup]:
        """Batch-выборка активных групп по id. Stale/soft-deleted отбрасываются."""
        if not group_ids:
            return []
        result = await self._db.scalars(
            select(UserGroup).where(
                UserGroup.id.in_(group_ids),
                UserGroup.is_active.is_(True),
            )
        )
        return list(result)

    async def create(
        self,
        department_id: str,
        name: str,
        display_name: str,
        description: str | None,
        created_by: str | None,
    ) -> UserGroup:
        obj = UserGroup(
            id=group_id(),
            department_id=department_id,
            name=name,
            display_name=display_name,
            description=description,
            created_by=created_by,
        )
        self._db.add(obj)
        await self._db.flush()
        return obj

    async def update(self, grp: UserGroup, display_name: str | None = None, description: str | None = None) -> None:
        if display_name is not None:
            grp.display_name = display_name
        if description is not None:
            grp.description = description
        await self._db.flush()

    async def deactivate(self, grp: UserGroup) -> None:
        grp.is_active = False
        await self._db.flush()

    # ── Membership ────────────────────────────────────────────────────────────

    async def get_membership(self, group_id_: str, user_id: str) -> UserGroupMembership | None:
        return await self._db.scalar(
            select(UserGroupMembership).where(
                UserGroupMembership.group_id == group_id_,
                UserGroupMembership.user_id == user_id,
            )
        )

    async def list_members(self, group_id_: str) -> list[UserGroupMembership]:
        result = await self._db.scalars(
            select(UserGroupMembership).where(UserGroupMembership.group_id == group_id_)
        )
        return list(result)

    async def list_user_groups(self, user_id: str) -> list[UserGroupMembership]:
        result = await self._db.scalars(
            select(UserGroupMembership).where(UserGroupMembership.user_id == user_id)
        )
        return list(result)

    async def add_member(self, group_id_: str, user_id: str, added_by: str | None) -> UserGroupMembership:
        m = UserGroupMembership(
            id=group_membership_id(),
            group_id=group_id_,
            user_id=user_id,
            added_by=added_by,
        )
        self._db.add(m)
        await self._db.flush()
        return m

    async def remove_member(self, membership: UserGroupMembership) -> None:
        await self._db.delete(membership)
        await self._db.flush()

    # ── Service access ────────────────────────────────────────────────────────

    async def get_service_access(self, group_id_: str, service_name: str) -> GroupServiceAccess | None:
        return await self._db.scalar(
            select(GroupServiceAccess).where(
                GroupServiceAccess.group_id == group_id_,
                GroupServiceAccess.service_name == service_name,
            )
        )

    async def list_service_access(self, group_id_: str) -> list[GroupServiceAccess]:
        result = await self._db.scalars(
            select(GroupServiceAccess).where(
                GroupServiceAccess.group_id == group_id_,
                GroupServiceAccess.is_active.is_(True),
            )
        )
        return list(result)

    async def list_service_access_for_groups(
        self, group_ids: list[str]
    ) -> dict[str, list[GroupServiceAccess]]:
        """Активный service-access по списку групп, сгруппированный по group_id.

        Один IN-запрос вместо per-group `list_service_access`.
        """
        if not group_ids:
            return {}
        rows = await self._db.scalars(
            select(GroupServiceAccess).where(
                GroupServiceAccess.group_id.in_(group_ids),
                GroupServiceAccess.is_active.is_(True),
            )
        )
        out: dict[str, list[GroupServiceAccess]] = {}
        for row in rows:
            out.setdefault(row.group_id, []).append(row)
        return out

    async def grant_service(self, group_id_: str, service_name: str, granted_by: str | None) -> GroupServiceAccess:
        obj = GroupServiceAccess(
            id=group_service_access_id(),
            group_id=group_id_,
            service_name=service_name,
            granted_by=granted_by,
        )
        self._db.add(obj)
        await self._db.flush()
        return obj

    async def revoke_service(self, access: GroupServiceAccess) -> None:
        access.is_active = False
        await self._db.flush()

    async def list_active_services_for_user(self, user_id: str) -> list[str]:
        """All services accessible to the user via their groups (active memberships + active access)."""
        memberships = await self._db.scalars(
            select(UserGroupMembership.group_id).where(UserGroupMembership.user_id == user_id)
        )
        group_ids = list(memberships)
        if not group_ids:
            return []
        rows = await self._db.scalars(
            select(GroupServiceAccess.service_name).where(
                GroupServiceAccess.group_id.in_(group_ids),
                GroupServiceAccess.is_active.is_(True),
            )
        )
        return list(set(rows))

    # ── Service roles ─────────────────────────────────────────────────────────

    async def list_roles(self, group_id_: str) -> list[GroupServiceRole]:
        result = await self._db.scalars(
            select(GroupServiceRole).where(
                GroupServiceRole.group_id == group_id_,
                GroupServiceRole.is_active.is_(True),
            )
        )
        return list(result)

    async def list_roles_for_groups(
        self, group_ids: list[str]
    ) -> dict[str, list[GroupServiceRole]]:
        """Активные service-роли по списку групп, сгруппированные по group_id.

        Один IN-запрос вместо per-group `list_roles`.
        """
        if not group_ids:
            return {}
        rows = await self._db.scalars(
            select(GroupServiceRole).where(
                GroupServiceRole.group_id.in_(group_ids),
                GroupServiceRole.is_active.is_(True),
            )
        )
        out: dict[str, list[GroupServiceRole]] = {}
        for row in rows:
            out.setdefault(row.group_id, []).append(row)
        return out

    async def set_roles(
        self, group_id_: str, service_name: str, roles: list[str], assigned_by: str | None
    ) -> None:
        await self._db.execute(
            delete(GroupServiceRole).where(
                GroupServiceRole.group_id == group_id_,
                GroupServiceRole.service_name == service_name,
            )
        )
        for role in roles:
            self._db.add(GroupServiceRole(
                id=group_service_role_id(),
                group_id=group_id_,
                service_name=service_name,
                role=role,
                assigned_by=assigned_by,
            ))
        await self._db.flush()

    async def clear_roles_for_service(self, group_id_: str, service_name: str) -> None:
        await self._db.execute(
            delete(GroupServiceRole).where(
                GroupServiceRole.group_id == group_id_,
                GroupServiceRole.service_name == service_name,
            )
        )
        await self._db.flush()

    async def deactivate_roles_by_role_name(self, service_name: str, role_name: str) -> None:
        rows = await self._db.scalars(
            select(GroupServiceRole).where(
                GroupServiceRole.service_name == service_name,
                GroupServiceRole.role == role_name,
            )
        )
        for row in rows:
            row.is_active = False
        await self._db.flush()

    async def deactivate_roles_by_role_name_in_dept(
        self, department_id: str, service_name: str, role_name: str
    ) -> None:
        """Deactivate (group, role) bindings only for groups in the given department."""
        rows = await self._db.scalars(
            select(GroupServiceRole)
            .join(UserGroup, UserGroup.id == GroupServiceRole.group_id)
            .where(
                UserGroup.department_id == department_id,
                GroupServiceRole.service_name == service_name,
                GroupServiceRole.role == role_name,
            )
        )
        for row in rows:
            row.is_active = False
        await self._db.flush()

    async def deactivate_all_dept_service_roles(
        self, department_id: str, service_name: str
    ) -> None:
        """Soft-delete every group→role binding for `service_name` in `department_id`."""
        rows = await self._db.scalars(
            select(GroupServiceRole)
            .join(UserGroup, UserGroup.id == GroupServiceRole.group_id)
            .where(
                UserGroup.department_id == department_id,
                GroupServiceRole.service_name == service_name,
            )
        )
        for row in rows:
            row.is_active = False
        await self._db.flush()

    async def get_roles_for_user(self, user_id: str) -> dict[str, list[str]]:
        """All service roles inherited by the user via their groups."""
        memberships = await self._db.scalars(
            select(UserGroupMembership.group_id).where(UserGroupMembership.user_id == user_id)
        )
        group_ids = list(memberships)
        if not group_ids:
            return {}
        rows = await self._db.scalars(
            select(GroupServiceRole).where(
                GroupServiceRole.group_id.in_(group_ids),
                GroupServiceRole.is_active.is_(True),
            )
        )
        result: dict[str, list[str]] = {}
        for row in rows:
            result.setdefault(row.service_name, []).append(row.role)
        # deduplicate
        return {k: list(set(v)) for k, v in result.items()}
