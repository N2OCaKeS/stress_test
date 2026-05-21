"""ServerAccount-репозиторий — CRUD + апдейт пароля."""

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import ServerAccount


async def get_by_id(db: AsyncSession, account_id: str) -> ServerAccount | None:
    """SELECT по PK."""
    stmt = select(ServerAccount).where(ServerAccount.id == account_id)
    return (await db.execute(stmt)).scalar_one_or_none()


async def list_for_server(
    db: AsyncSession, server_id: str, limit: int = 100, offset: int = 0
) -> list[ServerAccount]:
    """Список аккаунтов одного сервера — упорядочен по created_at DESC."""
    stmt = (
        select(ServerAccount)
        .where(ServerAccount.server_id == server_id)
        .order_by(ServerAccount.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return list((await db.execute(stmt)).scalars())


async def count_for_server(db: AsyncSession, server_id: str) -> int:
    """COUNT для пагинации."""
    stmt = select(func.count(ServerAccount.id)).where(ServerAccount.server_id == server_id)
    return int((await db.execute(stmt)).scalar_one())


async def create(db: AsyncSession, data: dict) -> ServerAccount:
    """INSERT новой строки. commit — на caller'е."""
    obj = ServerAccount(**data)
    db.add(obj)
    await db.flush()
    return obj


async def update(db: AsyncSession, obj: ServerAccount, changes: dict) -> ServerAccount:
    """In-place setattr + flush. commit — на caller'е."""
    for key, value in changes.items():
        setattr(obj, key, value)
    await db.flush()
    return obj


async def delete(db: AsyncSession, obj: ServerAccount) -> None:
    """DELETE объекта. commit — на caller'е."""
    await db.delete(obj)
    await db.flush()


async def update_password(
    db: AsyncSession, account: ServerAccount, password_encrypted: str
) -> ServerAccount:
    """Сменить пароль + проставить `password_rotated_at = now (UTC)`. commit — на caller'е."""
    account.password_encrypted = password_encrypted
    account.password_rotated_at = datetime.now(timezone.utc)
    await db.flush()
    return account
