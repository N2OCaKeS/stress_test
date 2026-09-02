"""CRUD для модели DeptGrant.

DeptGrant — флаг «recipient_dept получил допуск к cross_department-креде»,
обязателен перед выдачей RoleACL внутри recipient'а. Удаляется руками
через `delete()` (revoke) или каскадно при удалении самой кред (FK).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import DeptGrant


async def get_for_cred(db: AsyncSession, cred_id: str) -> list[DeptGrant]:
    """Все grants кред — для эндпоинта `GET /credentials/{id}/dept-grants`."""
    stmt = (
        select(DeptGrant)
        .where(DeptGrant.cred_id == cred_id)
        .order_by(DeptGrant.granted_at.asc(), DeptGrant.id.asc())
    )
    return list((await db.execute(stmt)).scalars())


async def exists_for(
    db: AsyncSession, cred_id: str, recipient_dept_id: str
) -> bool:
    """Есть ли DeptGrant(cred, dept) — для проверки в access_service.check_access."""
    stmt = select(DeptGrant.id).where(
        DeptGrant.cred_id == cred_id,
        DeptGrant.recipient_dept_id == recipient_dept_id,
    )
    return (await db.execute(stmt)).first() is not None


async def find(
    db: AsyncSession, cred_id: str, recipient_dept_id: str
) -> DeptGrant | None:
    """Точечный поиск — нужен для revoke (нужен объект, не bool)."""
    stmt = select(DeptGrant).where(
        DeptGrant.cred_id == cred_id,
        DeptGrant.recipient_dept_id == recipient_dept_id,
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def create(db: AsyncSession, **fields) -> DeptGrant:
    """INSERT нового grant'а. commit — на caller'е."""
    obj = DeptGrant(**fields)
    db.add(obj)
    await db.flush()
    return obj


async def delete(db: AsyncSession, grant: DeptGrant) -> None:
    """DELETE одного grant'а. Caller выше обязан каскадно снести RoleACL'и
    (см. role_acls.delete_for_cred_dept) — БД-каскада тут нет: RoleACL живёт
    в pair (cred, dept), а не на FK к DeptGrant."""
    await db.delete(grant)
    await db.flush()


async def delete_for_cred(db: AsyncSession, cred_id: str) -> int:
    """Хелпер для ручного «прибрать всё» — на практике перекрывается FK CASCADE.

    Возвращает число удалённых grants. Используется если надо снести grants
    без удаления самой кред (например, при demote cross_dep → department).
    """
    from sqlalchemy import delete as sa_delete

    stmt = sa_delete(DeptGrant).where(DeptGrant.cred_id == cred_id)
    result = await db.execute(stmt)
    await db.flush()
    return result.rowcount or 0
