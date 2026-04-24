"""Ban repository."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.ban import Ban
from src.utils.ids import ban_id
from src.utils.time import utcnow


class BanRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def get_active_ban(self, user_id: str) -> Ban | None:
        return await self._db.scalar(
            select(Ban).where(
                Ban.user_id == user_id,
                Ban.is_active.is_(True),
            )
        )

    async def create(
        self,
        user_id: str,
        banned_by: str,
        ban_type: str,
        reason: str | None = None,
        expires_at=None,
    ) -> Ban:
        ban = Ban(
            id=ban_id(),
            user_id=user_id,
            banned_by=banned_by,
            ban_type=ban_type,
            reason=reason,
            expires_at=expires_at,
        )
        self._db.add(ban)
        await self._db.flush()
        return ban

    async def deactivate(self, ban: Ban, unbanned_by: str) -> None:
        ban.is_active = False
        ban.unbanned_at = utcnow()
        ban.unbanned_by = unbanned_by
        await self._db.flush()
