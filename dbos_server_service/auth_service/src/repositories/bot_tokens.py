"""Bot token repository."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.bot_token import BotToken
from src.utils.ids import bot_token_id
from src.utils.time import utcnow


class BotTokenRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def get_by_id(self, tid: str) -> BotToken | None:
        return await self._db.get(BotToken, tid)

    async def get_active_by_hash(self, token_hash: str) -> BotToken | None:
        return await self._db.scalar(
            select(BotToken).where(
                BotToken.token_hash == token_hash,
                BotToken.revoked_at.is_(None),
            )
        )

    async def list_for_bot(self, bot_id: str) -> list[BotToken]:
        result = await self._db.scalars(
            select(BotToken)
            .where(BotToken.bot_id == bot_id)
            .order_by(BotToken.created_at.desc())
        )
        return list(result)

    async def exists_name(self, bot_id: str, name: str) -> bool:
        return await self._db.scalar(
            select(BotToken.id).where(
                BotToken.bot_id == bot_id,
                BotToken.name == name,
            )
        ) is not None

    async def create(
        self,
        bot_id: str,
        name: str,
        token_hash: str,
        token_prefix: str,
        expires_at=None,
    ) -> BotToken:
        token = BotToken(
            id=bot_token_id(),
            bot_id=bot_id,
            name=name,
            token_hash=token_hash,
            token_prefix=token_prefix,
            expires_at=expires_at,
        )
        self._db.add(token)
        await self._db.flush()
        return token

    async def revoke(self, token: BotToken) -> None:
        token.revoked_at = utcnow()
        await self._db.flush()

    async def touch(self, token: BotToken) -> None:
        token.last_used_at = utcnow()
        await self._db.flush()
