"""DAO для `BotAccount` — CRUD bot-accounts."""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.bot_account import BotAccount
from src.utils.ids import bot_id


class BotRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def get_by_id(self, bid: str) -> BotAccount | None:
        return await self._db.get(BotAccount, bid)

    async def list_by_department(
        self, department_id: str, limit: int | None = None, offset: int = 0
    ) -> list[BotAccount]:
        stmt = (
            select(BotAccount)
            .where(BotAccount.department_id == department_id)
            .order_by(BotAccount.created_at, BotAccount.id)
        )
        if limit is not None:
            stmt = stmt.limit(limit).offset(offset)
        result = await self._db.scalars(stmt)
        return list(result)

    async def list_all(
        self, limit: int | None = None, offset: int = 0
    ) -> list[BotAccount]:
        stmt = select(BotAccount).order_by(BotAccount.created_at, BotAccount.id)
        if limit is not None:
            stmt = stmt.limit(limit).offset(offset)
        result = await self._db.scalars(stmt)
        return list(result)

    async def list_by_creator(self, creator_id: str) -> list[BotAccount]:
        """Боты, созданные конкретным юзером (`created_by`).

        Узкая выборка для ban'а — без full scan таблицы `bot_accounts`.
        """
        result = await self._db.scalars(
            select(BotAccount).where(BotAccount.created_by == creator_id)
        )
        return list(result)

    async def count_all(self) -> int:
        return await self._db.scalar(
            select(func.count()).select_from(BotAccount)
        ) or 0

    async def count_by_department(self, department_id: str) -> int:
        return await self._db.scalar(
            select(func.count())
            .select_from(BotAccount)
            .where(BotAccount.department_id == department_id)
        ) or 0

    async def create(
        self,
        name: str,
        department_id: str,
        allowed_services: list[str],
        description: str | None = None,
        created_by: str | None = None,
    ) -> BotAccount:
        bot = BotAccount(
            id=bot_id(),
            name=name,
            department_id=department_id,
            allowed_services=allowed_services,
            description=description,
            created_by=created_by,
        )
        self._db.add(bot)
        await self._db.flush()
        return bot

    async def update(self, bot: BotAccount, **kwargs) -> BotAccount:
        for key, value in kwargs.items():
            setattr(bot, key, value)
        await self._db.flush()
        return bot
