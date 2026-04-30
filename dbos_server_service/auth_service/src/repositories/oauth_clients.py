"""OAuth2 client and authorization code repository."""

import secrets

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.oauth_authorization_code import OAuthAuthorizationCode
from src.models.oauth_client import OAuthClient
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
    ) -> OAuthAuthorizationCode:
        code = OAuthAuthorizationCode(
            id=oauth_code_id(),
            code_hash=code_hash,
            client_id=client_id,
            user_id=user_id,
            redirect_uri=redirect_uri,
            scopes=scopes,
            expires_at=expires_at,
        )
        self._db.add(code)
        await self._db.flush()
        return code

    async def mark_used(self, code: OAuthAuthorizationCode) -> None:
        code.used_at = utcnow()
        await self._db.flush()
