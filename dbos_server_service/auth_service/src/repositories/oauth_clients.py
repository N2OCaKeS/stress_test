"""DAO для `OAuthClient` + `OAuthAuthorizationCode` — CRUD клиентов и CAS mark_used кодов."""

import secrets

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.oauth_authorization_code import OAuthAuthorizationCode
from src.models.oauth_client import OAuthClient
from src.repositories._cas import atomic_transition
from src.utils.ids import oauth_client_id, oauth_code_id
from src.utils.time import utcnow


class OAuthClientRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def get_by_id(self, cid: str) -> OAuthClient | None:
        return await self._db.get(OAuthClient, cid)

    async def get_by_client_id(self, client_id: str) -> OAuthClient | None:
        return await self._db.scalar(select(OAuthClient).where(OAuthClient.client_id == client_id))

    async def list_by_department(self, department_id: str) -> list[OAuthClient]:
        result = await self._db.scalars(
            select(OAuthClient).where(
                OAuthClient.department_id == department_id,
                OAuthClient.is_active.is_(True),
            )
        )
        return list(result)

    async def exists_name(self, department_id: str, name: str) -> bool:
        return await self._db.scalar(
            select(OAuthClient.id).where(
                OAuthClient.department_id == department_id,
                OAuthClient.name == name,
                OAuthClient.is_active.is_(True),
            )
        ) is not None

    async def create(
        self,
        department_id: str,
        name: str,
        client_secret_hash: str,
        client_secret_prefix: str,
        redirect_uris: list[str],
        allowed_scopes: list[str],
        grant_types: list[str],
        description: str | None = None,
        created_by: str | None = None,
    ) -> OAuthClient:
        client = OAuthClient(
            id=oauth_client_id(),
            client_id=f"cli_{secrets.token_urlsafe(16)}",
            client_secret_hash=client_secret_hash,
            client_secret_prefix=client_secret_prefix,
            department_id=department_id,
            name=name,
            description=description,
            redirect_uris=redirect_uris,
            allowed_scopes=allowed_scopes,
            grant_types=grant_types,
            created_by=created_by,
        )
        self._db.add(client)
        await self._db.flush()
        return client

    async def list_all(self) -> list[OAuthClient]:
        result = await self._db.scalars(
            select(OAuthClient).where(OAuthClient.is_active.is_(True))
        )
        return list(result)

    async def deactivate(self, client: OAuthClient) -> None:
        client.is_active = False
        await self._db.flush()


class OAuthCodeRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def get_by_hash(self, code_hash: str) -> OAuthAuthorizationCode | None:
        return await self._db.scalar(
            select(OAuthAuthorizationCode).where(
                OAuthAuthorizationCode.code_hash == code_hash,
                OAuthAuthorizationCode.used_at.is_(None),
            )
        )

    async def create(
        self,
        client_id: str,
        user_id: str,
        code_hash: str,
        redirect_uri: str,
        scopes: list[str],
        expires_at,
        code_challenge: str | None = None,
        code_challenge_method: str | None = None,
    ) -> OAuthAuthorizationCode:
        code = OAuthAuthorizationCode(
            id=oauth_code_id(),
            code_hash=code_hash,
            client_id=client_id,
            user_id=user_id,
            redirect_uri=redirect_uri,
            scopes=scopes,
            expires_at=expires_at,
            code_challenge=code_challenge,
            code_challenge_method=code_challenge_method,
        )
        self._db.add(code)
        await self._db.flush()
        return code

    async def mark_used(self, code: OAuthAuthorizationCode) -> bool:
        """Atomically mark this authorization code as used (compare-and-swap).

        Two parallel ``exchange_code`` calls with the same ``code`` would both
        see ``used_at IS NULL`` in :meth:`get_by_hash` and, before this fix,
        both happily proceeded to issue a JWT — classic OAuth code-replay.

        Same CAS shape as :class:`BanRepository.deactivate` and
        :class:`SessionRepository.rotate` (общий helper —
        :func:`src.repositories._cas.atomic_transition`): один
        ``UPDATE … WHERE id = :id AND used_at IS NULL RETURNING id``
        даёт ровно одному из N concurrent worker'ов «выиграть» — Postgres
        сам сериализует row-level lock. Winner получает non-empty
        ``RETURNING`` и эмитит JWT; losers возвращают ``False`` и caller
        бросает ``INVALID_GRANT``.

        Returns
        -------
        bool
            ``True`` if this caller transitioned the row (``used_at`` was
            ``NULL``, now set to ``now``), ``False`` if another worker already
            consumed the code (or the row no longer exists).
        """
        now = utcnow()
        won = await atomic_transition(
            self._db,
            OAuthAuthorizationCode,
            id_column="id",
            id_value=code.id,
            where_clause=OAuthAuthorizationCode.used_at.is_(None),
            update_values={"used_at": now},
        )
        if not won:
            return False
        # Keep in-memory ORM-instance in sync with the row we just wrote —
        # audit-detail building in `exchange_code` may inspect this object
        # downstream.
        code.used_at = now
        return True
