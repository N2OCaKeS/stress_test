"""Personal access token repository."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.personal_access_token import PersonalAccessToken
from src.utils.ids import pat_id
from src.utils.time import utcnow


class TokenRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def get_by_id(self, tid: str) -> PersonalAccessToken | None:
        return await self._db.get(PersonalAccessToken, tid)

    async def get_active_by_hash(self, token_hash: str) -> PersonalAccessToken | None:
        return await self._db.scalar(
            select(PersonalAccessToken).where(
                PersonalAccessToken.token_hash == token_hash,
                PersonalAccessToken.revoked_at.is_(None),
            )
        )

    async def list_for_user(self, user_id: str) -> list[PersonalAccessToken]:
        result = await self._db.scalars(
            select(PersonalAccessToken)
            .where(PersonalAccessToken.user_id == user_id)
            .order_by(PersonalAccessToken.created_at.desc())
        )
        return list(result)

    async def exists_name(self, user_id: str, name: str) -> bool:
        return await self._db.scalar(
            select(PersonalAccessToken.id).where(
                PersonalAccessToken.user_id == user_id,
                PersonalAccessToken.name == name,
            )
        ) is not None

    async def create(
        self,
        user_id: str,
        name: str,
        token_hash: str,
        token_prefix: str,
        allowed_services: list[str],
        expires_at=None,
    ) -> PersonalAccessToken:
        pat = PersonalAccessToken(
            id=pat_id(),
            user_id=user_id,
            name=name,
            token_hash=token_hash,
            token_prefix=token_prefix,
            allowed_services=allowed_services,
            expires_at=expires_at,
        )
        self._db.add(pat)
        await self._db.flush()
        return pat

    async def revoke(self, pat: PersonalAccessToken) -> None:
        pat.revoked_at = utcnow()
        await self._db.flush()

    async def revoke_all_for_user(self, user_id: str) -> None:
        now = utcnow()
        result = await self._db.scalars(
            select(PersonalAccessToken).where(
                PersonalAccessToken.user_id == user_id,
                PersonalAccessToken.revoked_at.is_(None),
            )
        )
        for pat in result:
            pat.revoked_at = now
        await self._db.flush()

    async def touch(self, pat: PersonalAccessToken) -> None:
        pat.last_used_at = utcnow()
        await self._db.flush()
