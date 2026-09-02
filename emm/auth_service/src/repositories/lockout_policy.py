"""DAO для `LockoutPolicy` — single-row override brute-force lockout-параметров."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.lockout_policy import SINGLETON_ID, LockoutPolicy


class LockoutPolicyRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def get(self) -> LockoutPolicy | None:
        """Вернуть единственную строку политики или None (= использовать env-дефолты)."""
        return await self._db.scalar(
            select(LockoutPolicy).where(LockoutPolicy.id == SINGLETON_ID)
        )

    async def upsert(
        self,
        max_failed_attempts: int,
        lockout_minutes: int,
        updated_by: str | None = None,
    ) -> LockoutPolicy:
        """Создать или обновить строку политики. Commit на caller'е."""
        row = await self.get()
        if row is None:
            row = LockoutPolicy(
                id=SINGLETON_ID,
                max_failed_attempts=max_failed_attempts,
                lockout_minutes=lockout_minutes,
                updated_by=updated_by,
            )
            self._db.add(row)
        else:
            row.max_failed_attempts = max_failed_attempts
            row.lockout_minutes = lockout_minutes
            row.updated_by = updated_by
        await self._db.flush()
        return row
