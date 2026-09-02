"""CRUD для модели CredentialUserACL.

Два основных шаблона: листинг user-ACL карточки кред (`get_for_cred`) и
точечная проверка доступа конкретного актора (`find` по `(cred_id, user_id)`).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import CredentialUserACL


async def get_for_cred(db: AsyncSession, cred_id: str) -> list[CredentialUserACL]:
    """Все user-ACL'и кред (для `GET /credentials/{id}/user-acl`)."""
    stmt = (
        select(CredentialUserACL)
        .where(CredentialUserACL.cred_id == cred_id)
        .order_by(CredentialUserACL.created_at.asc(), CredentialUserACL.id.asc())
    )
    return list((await db.execute(stmt)).scalars())


async def find(
    db: AsyncSession, cred_id: str, user_id: str
) -> CredentialUserACL | None:
    """Точечный поиск по UNIQUE-ключу — проверка доступа конкретного актора."""
    stmt = select(CredentialUserACL).where(
        CredentialUserACL.cred_id == cred_id,
        CredentialUserACL.user_id == user_id,
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def create(db: AsyncSession, **fields) -> CredentialUserACL:
    """INSERT нового user-ACL. commit — на caller'е."""
    obj = CredentialUserACL(**fields)
    db.add(obj)
    await db.flush()
    return obj


async def delete(db: AsyncSession, acl: CredentialUserACL) -> None:
    """DELETE одного user-ACL."""
    await db.delete(acl)
    await db.flush()
