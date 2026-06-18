"""Репозиторий per-account ACL (server_account_user_acl).

Прямые гранты пользователя на учётку. Lookup'ы для проверки прав
(`get_for_account_user`) и CRUD для управления (list/upsert/delete).
"""

from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.constants import ACCOUNT_ACL_ACTION_COLUMNS
from src.models import ServerAccountUserAcl


async def get_for_account_user(
    db: AsyncSession, account_id: str, user_id: str
) -> ServerAccountUserAcl | None:
    """Найти грант по паре (account_id, user_id) — основной lookup проверки прав."""
    stmt = select(ServerAccountUserAcl).where(
        ServerAccountUserAcl.account_id == account_id,
        ServerAccountUserAcl.user_id == user_id,
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def list_for_account(
    db: AsyncSession, account_id: str
) -> list[ServerAccountUserAcl]:
    """Все гранты учётки, отсортированные по (created_at, user_id)."""
    stmt = (
        select(ServerAccountUserAcl)
        .where(ServerAccountUserAcl.account_id == account_id)
        .order_by(
            ServerAccountUserAcl.created_at,
            ServerAccountUserAcl.user_id,
        )
    )
    return list((await db.execute(stmt)).scalars())


async def upsert(
    db: AsyncSession,
    *,
    acl_id: str,
    account_id: str,
    user_id: str,
    department_id: str,
    actions: set[str],
    created_by: str | None,
) -> ServerAccountUserAcl:
    """Выдать/перевыдать грант: создать строку либо переписать набор флагов.

    `actions` — подмножество ACCOUNT_ACL_ACTIONS. Повторная выдача той же паре
    (account_id, user_id) заменяет набор действий целиком (это не deny —
    оператор задаёт полный желаемый набор). commit — на caller'е.
    """
    flags = {col: (action in actions) for action, col in ACCOUNT_ACL_ACTION_COLUMNS.items()}
    obj = await get_for_account_user(db, account_id, user_id)
    if obj is None:
        obj = ServerAccountUserAcl(
            id=acl_id,
            account_id=account_id,
            user_id=user_id,
            department_id=department_id,
            created_by=created_by,
            **flags,
        )
        db.add(obj)
    else:
        for col, value in flags.items():
            setattr(obj, col, value)
    await db.flush()
    return obj


async def delete(db: AsyncSession, account_id: str, user_id: str) -> int:
    """DELETE гранта по (account_id, user_id). Возвращает число удалённых строк."""
    stmt = sa_delete(ServerAccountUserAcl).where(
        ServerAccountUserAcl.account_id == account_id,
        ServerAccountUserAcl.user_id == user_id,
    )
    result = await db.execute(stmt)
    return result.rowcount or 0
