"""DAO для `UserServiceRole` — set/clear/list ролей юзера + bulk-операции."""

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.user import User
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

    async def list_active_assignments(self, user_id: str) -> list[UserServiceRole]:
        """Return raw `UserServiceRole` rows (active only) for the user.

        Used by `GET /users/{id}/permissions` where the UI needs
        ``assigned_at`` / ``assigned_by`` per assignment — info that
        :meth:`get_all_roles` strips when collapsing to
        ``{service_name: [role, ...]}``. Active-only filter mirrors
        :meth:`get_all_roles` so the two stay consistent.
        """
        rows = await self._db.scalars(
            select(UserServiceRole).where(
                UserServiceRole.user_id == user_id,
                UserServiceRole.is_active.is_(True),
            )
        )
        return list(rows)

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

    async def deactivate_all_for_service(self, service_name: str) -> list[str]:
        """Снять все user-роли на сервисе. Возвращает user_id, у которых хотя
        бы одна роль действительно была деактивирована — caller использует
        список для сброса identity-кэша (без него юзер до TTL продолжает
        видеть роль в introspect).
        """
        rows = await self._db.scalars(
            select(UserServiceRole).where(UserServiceRole.service_name == service_name)
        )
        affected: set[str] = set()
        for row in rows:
            if row.is_active:
                affected.add(row.user_id)
            row.is_active = False
        await self._db.flush()
        return list(affected)

    async def deactivate_all_for_user(self, user_id: str) -> int:
        """Снять все service-роли с юзера. Возвращает count затронутых строк.

        Используется при смене `department_id` — старые роли указывают на
        сервисы прежнего отдела и их нельзя сохранять. `_merge_permissions`
        INTERSECT уже отфильтровывает их из effective view, но физически
        строки остаются в БД — это нарушает инвариант
        «роль юзера ⊆ сервисы его отдела».
        """
        rows = await self._db.scalars(
            select(UserServiceRole).where(
                UserServiceRole.user_id == user_id,
                UserServiceRole.is_active.is_(True),
            )
        )
        count = 0
        for row in rows:
            row.is_active = False
            count += 1
        await self._db.flush()
        return count

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

    async def deactivate_by_role_name_in_dept(
        self, department_id: str, service_name: str, role_name: str
    ) -> list[str]:
        """Deactivate user→role assignments only for users of the given department.

        Возвращает список user_id, у которых роль действительно была снята —
        нужен caller'у (`service_role_service.delete_role`) чтобы сбросить
        identity-кэш этих юзеров и не дать им до TTL увидеть удалённую роль.

        Один UPDATE-WHERE ... RETURNING — раньше тащили все строки в Python
        и руками синкали `is_active`.
        """
        user_ids_stmt = select(User.id).where(User.department_id == department_id)
        result = await self._db.scalars(
            update(UserServiceRole)
            .where(
                UserServiceRole.user_id.in_(user_ids_stmt),
                UserServiceRole.service_name == service_name,
                UserServiceRole.role == role_name,
                UserServiceRole.is_active.is_(True),
            )
            .values(is_active=False)
            .returning(UserServiceRole.user_id)
        )
        affected = list(result)
        await self._db.flush()
        return affected

    async def deactivate_all_in_dept_for_service(
        self, department_id: str, service_name: str
    ) -> list[str]:
        """Deactivate every role for every user of `department_id` in `service_name`.

        Used when a department's access to a service is revoked. Возвращает
        список user_id, у которых хотя бы одна роль реально была снята —
        caller использует его, чтобы сбросить identity-кэш этих юзеров.
        """
        rows = await self._db.scalars(
            select(UserServiceRole)
            .join(User, User.id == UserServiceRole.user_id)
            .where(
                User.department_id == department_id,
                UserServiceRole.service_name == service_name,
            )
        )
        affected: set[str] = set()
        for row in rows:
            if row.is_active:
                affected.add(row.user_id)
            row.is_active = False
        await self._db.flush()
        return list(affected)

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
