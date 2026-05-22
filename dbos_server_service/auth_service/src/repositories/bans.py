"""DAO для `Ban` — CRUD + CAS-deactivate для concurrent unban."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.ban import Ban
from src.repositories._cas import atomic_transition
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

    async def deactivate(self, ban: Ban, unbanned_by: str | None) -> bool:
        """Атомарный deactivate через CAS `WHERE is_active = TRUE`.

        Возвращает `True` если caller реально перевёл строку (была активна,
        стала неактивна), `False` — если другой worker уже её deactivate'нул
        (audit/side-effects в таком случае эмитить НЕ нужно).

        Один SQL-запрос (см. `_cas.atomic_transition`) позволяет двум
        concurrent `auto_unban_if_expired` worker'ам безопасно гонкуться:
        winner получает non-empty `RETURNING` и эмитит `user.unban`.

        `unbanned_by=None` теперь first-class (колонка nullable) — system /
        auto-unban caller'ы должны передавать `None`, а не legacy `""`-sentinel,
        иначе `unbanned_by IS NULL` фильтры промахиваются.
        """
        now = utcnow()
        won = await atomic_transition(
            self._db,
            Ban,
            id_column="id",
            id_value=ban.id,
            where_clause=Ban.is_active.is_(True),
            update_values={
                "is_active": False,
                "unbanned_at": now,
                "unbanned_by": unbanned_by,
            },
        )
        if not won:
            return False
        # Синкаем in-memory ORM-инстанс — caller'ы (например `unban_user`)
        # потом читают `ban.reason`/`ban.id` для audit details.
        ban.is_active = False
        ban.unbanned_at = now
        ban.unbanned_by = unbanned_by
        return True
