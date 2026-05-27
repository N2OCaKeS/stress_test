"""ServerAccount-репозиторий — CRUD + апдейт пароля + M2M-линковка серверов."""

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import ServerAccount, ServerAccountServer
from src.utils.ids import server_account_server_id


async def get_by_id(db: AsyncSession, account_id: str) -> ServerAccount | None:
    """SELECT по PK. server_links подтягиваются selectin'ом."""
    stmt = select(ServerAccount).where(ServerAccount.id == account_id)
    return (await db.execute(stmt)).scalar_one_or_none()


def linked_server_ids(account: ServerAccount) -> list[str]:
    """Список server_id из связок аккаунта, упорядоченный по времени привязки."""
    return [
        link.server_id
        for link in sorted(account.server_links, key=lambda link: link.created_at)
    ]


async def list_for_server(
    db: AsyncSession, server_id: str, limit: int = 100, offset: int = 0
) -> list[ServerAccount]:
    """Список аккаунтов, привязанных к серверу — упорядочен по created_at DESC."""
    stmt = (
        select(ServerAccount)
        .join(ServerAccountServer, ServerAccountServer.account_id == ServerAccount.id)
        .where(ServerAccountServer.server_id == server_id)
        .order_by(ServerAccount.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return list((await db.execute(stmt)).scalars())


async def count_for_server(db: AsyncSession, server_id: str) -> int:
    """COUNT привязанных к серверу аккаунтов (для пагинации)."""
    stmt = (
        select(func.count(ServerAccountServer.id))
        .where(ServerAccountServer.server_id == server_id)
    )
    return int((await db.execute(stmt)).scalar_one())


async def create(db: AsyncSession, data: dict, server_ids: list[str]) -> ServerAccount:
    """INSERT строки аккаунта + связок на каждый сервер. commit — на caller'е."""
    obj = ServerAccount(**data)
    db.add(obj)
    await db.flush()
    for sid in server_ids:
        db.add(
            ServerAccountServer(
                id=server_account_server_id(),
                account_id=obj.id,
                server_id=sid,
                login=obj.login,
            )
        )
    await db.flush()
    return obj


async def update(db: AsyncSession, obj: ServerAccount, changes: dict) -> ServerAccount:
    """In-place setattr + flush. commit — на caller'е."""
    for key, value in changes.items():
        setattr(obj, key, value)
    await db.flush()
    return obj


async def delete(db: AsyncSession, obj: ServerAccount) -> None:
    """DELETE объекта (связки уходят каскадом). commit — на caller'е."""
    await db.delete(obj)
    await db.flush()


async def add_servers(
    db: AsyncSession, account: ServerAccount, server_ids: list[str]
) -> None:
    """Привязать аккаунт к новым серверам. Уже привязанные — пропускаем.

    commit/flush — на caller'е (через последующий flush). Дубль по
    (account_id, server_id) отсеивается на стороне Python; новый логин на
    занятом сервере поднимет IntegrityError на uq_server_login.
    """
    existing = {link.server_id for link in account.server_links}
    for sid in server_ids:
        if sid in existing:
            continue
        db.add(
            ServerAccountServer(
                id=server_account_server_id(),
                account_id=account.id,
                server_id=sid,
                login=account.login,
            )
        )
    await db.flush()
    await db.refresh(account)


async def remove_servers(
    db: AsyncSession, account: ServerAccount, server_ids: list[str]
) -> int:
    """Отвязать аккаунт от серверов. Возвращает число снятых связок."""
    targets = set(server_ids)
    removed = 0
    for link in list(account.server_links):
        if link.server_id in targets:
            await db.delete(link)
            removed += 1
    await db.flush()
    await db.refresh(account)
    return removed


async def is_linked(db: AsyncSession, account_id: str, server_id: str) -> bool:
    """Привязан ли аккаунт к конкретному серверу."""
    stmt = select(ServerAccountServer.id).where(
        ServerAccountServer.account_id == account_id,
        ServerAccountServer.server_id == server_id,
    )
    return (await db.execute(stmt)).first() is not None


async def update_password(
    db: AsyncSession, account: ServerAccount, password_encrypted: str
) -> ServerAccount:
    """Сменить пароль + проставить `password_rotated_at = now (UTC)`. commit — на caller'е."""
    account.password_encrypted = password_encrypted
    account.password_rotated_at = datetime.now(timezone.utc)
    await db.flush()
    return account
