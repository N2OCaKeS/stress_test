"""Session repository."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.session import Session
from src.utils.ids import session_id
from src.utils.time import utcnow


class SessionRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def get_by_id(self, sid: str) -> Session | None:
        return await self._db.get(Session, sid)

    async def get_active_by_token_hash(self, token_hash: str) -> Session | None:
        return await self._db.scalar(
            select(Session).where(
                Session.refresh_token_hash == token_hash,
                Session.is_active.is_(True),
            )
        )

    async def get_by_token_hash(self, token_hash: str) -> Session | None:
        """Lookup by current or previous token hash — used for reuse detection."""
        return await self._db.scalar(
            select(Session).where(
                (Session.refresh_token_hash == token_hash) |
                (Session.previous_token_hash == token_hash)
            )
        )

    async def create(
        self,
        user_id: str,
        refresh_token_hash: str,
        expires_at,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> Session:
        sess = Session(
            id=session_id(),
            user_id=user_id,
            refresh_token_hash=refresh_token_hash,
            expires_at=expires_at,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self._db.add(sess)
        await self._db.flush()
        return sess

    async def rotate(self, sess: Session, new_hash: str, new_expires_at) -> Session:
        """Replace token hash and increment generation counter."""
        sess.previous_token_hash = sess.refresh_token_hash
        sess.refresh_token_hash = new_hash
        sess.token_generation += 1
        sess.expires_at = new_expires_at
        sess.last_used_at = utcnow()
        await self._db.flush()
        return sess

    async def revoke(self, sess: Session) -> None:
        sess.is_active = False
        sess.revoked_at = utcnow()
        await self._db.flush()

    async def revoke_all_for_user(self, user_id: str) -> None:
        now = utcnow()
        result = await self._db.scalars(
            select(Session).where(
                Session.user_id == user_id,
                Session.is_active.is_(True),
            )
        )
        for sess in result:
            sess.is_active = False
            sess.revoked_at = now
        await self._db.flush()

    async def mark_suspicious(self, sess: Session) -> None:
        sess.is_suspicious = True
        await self._db.flush()
