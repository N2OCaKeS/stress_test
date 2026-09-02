"""Репозиторий ignore-list'а логинов отдела (server_account_ignored_login)."""

from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import ServerAccountIgnoredLogin


async def list_for_department(
    db: AsyncSession, department_id: str
) -> list[ServerAccountIgnoredLogin]:
    """Все игнор-логины отдела, отсортированные по login."""
    stmt = (
        select(ServerAccountIgnoredLogin)
        .where(ServerAccountIgnoredLogin.department_id == department_id)
        .order_by(ServerAccountIgnoredLogin.login)
    )
    return list((await db.execute(stmt)).scalars())


async def ignored_logins_for_department(
    db: AsyncSession, department_id: str
) -> set[str]:
    """Множество игнор-логинов отдела — для reconcile/discovery-фильтра.

    Берём только колонку login одним SELECT'ом, чтобы reconcile не тащил
    лишние поля и собирал set за один заход.
    """
    stmt = select(ServerAccountIgnoredLogin.login).where(
        ServerAccountIgnoredLogin.department_id == department_id
    )
    return {row[0] for row in (await db.execute(stmt)).all()}


async def get(
    db: AsyncSession, department_id: str, login: str
) -> ServerAccountIgnoredLogin | None:
    """Найти запись по (department_id, login)."""
    stmt = select(ServerAccountIgnoredLogin).where(
        ServerAccountIgnoredLogin.department_id == department_id,
        ServerAccountIgnoredLogin.login == login,
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def create(
    db: AsyncSession, data: dict
) -> ServerAccountIgnoredLogin:
    """INSERT записи игнор-логина. commit — на caller'е."""
    obj = ServerAccountIgnoredLogin(**data)
    db.add(obj)
    await db.flush()
    return obj


async def delete(
    db: AsyncSession, department_id: str, login: str
) -> int:
    """DELETE записи по (department_id, login). Возвращает число удалённых строк."""
    stmt = sa_delete(ServerAccountIgnoredLogin).where(
        ServerAccountIgnoredLogin.department_id == department_id,
        ServerAccountIgnoredLogin.login == login,
    )
    result = await db.execute(stmt)
    return result.rowcount or 0
