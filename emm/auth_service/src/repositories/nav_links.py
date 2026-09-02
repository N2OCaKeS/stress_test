"""DAO для `NavLink` — single-row конфиг настраиваемой кнопки левой панели."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.nav_link import SINGLETON_ID, NavLink


class NavLinkRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def get(self) -> NavLink | None:
        """Вернуть единственную строку конфига или None (= кнопка не настроена)."""
        return await self._db.scalar(
            select(NavLink).where(NavLink.id == SINGLETON_ID)
        )

    async def upsert(
        self,
        *,
        enabled: bool,
        label: str,
        url: str | None,
        all_departments: bool,
        department_ids: list[str],
        updated_by: str | None = None,
    ) -> NavLink:
        """Создать или обновить строку конфига. Commit на caller'е."""
        row = await self.get()
        if row is None:
            row = NavLink(
                id=SINGLETON_ID,
                enabled=enabled,
                label=label,
                url=url,
                all_departments=all_departments,
                department_ids=department_ids,
                updated_by=updated_by,
            )
            self._db.add(row)
        else:
            row.enabled = enabled
            row.label = label
            row.url = url
            row.all_departments = all_departments
            row.department_ids = department_ids
            row.updated_by = updated_by
        await self._db.flush()
        return row
