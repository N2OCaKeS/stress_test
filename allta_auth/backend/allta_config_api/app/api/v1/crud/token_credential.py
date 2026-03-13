from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.models.token_credential import TokenCredential


async def list_token_credentials(db: AsyncSession) -> list[TokenCredential]:
    stmt = select(TokenCredential).order_by(TokenCredential.token_key.asc())
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_token_credential_by_key(
    db: AsyncSession,
    token_key: str,
) -> TokenCredential | None:
    stmt = select(TokenCredential).where(TokenCredential.token_key == token_key)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def upsert_token_credential(
    db: AsyncSession,
    *,
    token_key: str,
    token_encrypted: str,
    updated_by: str | None,
) -> tuple[TokenCredential, bool]:
    existing = await get_token_credential_by_key(db, token_key)
    if existing:
        existing.token_encrypted = token_encrypted
        existing.updated_by = updated_by
        await db.commit()
        await db.refresh(existing)
        return existing, False

    item = TokenCredential(
        token_key=token_key,
        token_encrypted=token_encrypted,
        updated_by=updated_by,
    )
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return item, True


async def update_token_credential(
    db: AsyncSession,
    *,
    token_key: str,
    token_encrypted: str,
    updated_by: str | None,
) -> TokenCredential | None:
    item = await get_token_credential_by_key(db, token_key)
    if not item:
        return None

    item.token_encrypted = token_encrypted
    item.updated_by = updated_by

    await db.commit()
    await db.refresh(item)
    return item


async def delete_token_credential(db: AsyncSession, token_key: str) -> bool:
    item = await get_token_credential_by_key(db, token_key)
    if not item:
        return False

    await db.delete(item)
    await db.commit()
    return True
