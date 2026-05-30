"""DAO для `BotServiceRole` — set/clear ролей бота по сервису + bulk-deactivate операции."""

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.bot_account import BotAccount
from src.models.bot_service_role import BotServiceRole
from src.utils.ids import bot_service_role_id


class BotRoleRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def get_all_roles(self, bot_id: str) -> dict[str, list[str]]:
        rows = await self._db.scalars(
            select(BotServiceRole).where(
                BotServiceRole.bot_id == bot_id,
                BotServiceRole.is_active.is_(True),
            )
        )
        out: dict[str, list[str]] = {}
        for row in rows:
            out.setdefault(row.service_name, []).append(row.role)
        return out

    async def get_roles_by_service(self, bot_id: str, service_name: str) -> list[str]:
        rows = await self._db.scalars(
            select(BotServiceRole.role).where(
                BotServiceRole.bot_id == bot_id,
                BotServiceRole.service_name == service_name,
                BotServiceRole.is_active.is_(True),
            )
        )
        return list(rows)

    async def set_roles(
        self,
        bot_id: str,
        service_name: str,
        roles: list[str],
        assigned_by: str | None,
    ) -> None:
        """Replace всех ролей для пары (bot, service) на переданный список."""
        await self._db.execute(
            delete(BotServiceRole).where(
                BotServiceRole.bot_id == bot_id,
                BotServiceRole.service_name == service_name,
            )
        )
        for role in roles:
            self._db.add(
                BotServiceRole(
                    id=bot_service_role_id(),
                    bot_id=bot_id,
                    service_name=service_name,
                    role=role,
                    assigned_by=assigned_by,
                )
            )
        await self._db.flush()

    async def clear_roles_for_service(self, bot_id: str, service_name: str) -> None:
        await self._db.execute(
            delete(BotServiceRole).where(
                BotServiceRole.bot_id == bot_id,
                BotServiceRole.service_name == service_name,
            )
        )
        await self._db.flush()

    async def deactivate_all_for_service(self, service_name: str) -> list[str]:
        """Деактивировать все bot→role связи для сервиса (across all depts).

        Возвращает уникальные bot_id затронутых ботов — caller использует это
        для сброса identity-cache.
        """
        rows = await self._db.scalars(
            select(BotServiceRole).where(BotServiceRole.service_name == service_name)
        )
        affected: set[str] = set()
        for row in rows:
            if row.is_active:
                affected.add(row.bot_id)
            row.is_active = False
        await self._db.flush()
        return list(affected)

    async def deactivate_by_role_name_in_dept(
        self, department_id: str, service_name: str, role_name: str
    ) -> list[str]:
        """Деактивировать bot→role-связи только для ботов указанного отдела.

        Возвращает bot_id затронутых ботов — caller использует это для bulk
        сброса identity-cache. Один UPDATE-WHERE ... RETURNING.
        """
        bot_ids_stmt = select(BotAccount.id).where(
            BotAccount.department_id == department_id
        )
        result = await self._db.scalars(
            update(BotServiceRole)
            .where(
                BotServiceRole.bot_id.in_(bot_ids_stmt),
                BotServiceRole.service_name == service_name,
                BotServiceRole.role == role_name,
                BotServiceRole.is_active.is_(True),
            )
            .values(is_active=False)
            .returning(BotServiceRole.bot_id)
        )
        affected = list(result)
        await self._db.flush()
        return affected

    async def deactivate_all_in_dept_for_service(
        self, department_id: str, service_name: str
    ) -> list[str]:
        """Деактивировать все bot→role связи для (dept, service).

        Возвращает уникальные bot_id затронутых ботов — caller использует это
        для сброса identity-cache.
        """
        rows = await self._db.scalars(
            select(BotServiceRole)
            .join(BotAccount, BotAccount.id == BotServiceRole.bot_id)
            .where(
                BotAccount.department_id == department_id,
                BotServiceRole.service_name == service_name,
            )
        )
        affected: set[str] = set()
        for row in rows:
            if row.is_active:
                affected.add(row.bot_id)
            row.is_active = False
        await self._db.flush()
        return list(affected)
