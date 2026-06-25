"""CRUD для модели RoleACL.

Запросы строго по (cred_id) и (cred_id, dept_id) — это два основных
шаблона: листинг ACL карточки кред и проверка доступа конкретного
актора (его dept).
"""

from __future__ import annotations

from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import RoleACL


async def get_for_cred(db: AsyncSession, cred_id: str) -> list[RoleACL]:
    """Все ACL'и кред (для эндпоинта `GET /credentials/{id}/acl`)."""
    stmt = (
        select(RoleACL)
        .where(RoleACL.cred_id == cred_id)
        .order_by(RoleACL.granted_at.asc(), RoleACL.id.asc())
    )
    return list((await db.execute(stmt)).scalars())


async def get_for_cred_dept(
    db: AsyncSession, cred_id: str, dept_id: str
) -> list[RoleACL]:
    """ACL'и кред, действующие в конкретном dep'е.

    Используется в проверке доступа: actor видит креду только если в его
    dep'е есть ACL под одну из его service-ролей.
    """
    stmt = (
        select(RoleACL)
        .where(RoleACL.cred_id == cred_id, RoleACL.dept_id == dept_id)
        .order_by(RoleACL.role_name.asc())
    )
    return list((await db.execute(stmt)).scalars())


async def find(
    db: AsyncSession, cred_id: str, dept_id: str, role_name: str
) -> RoleACL | None:
    """Точечный поиск по UNIQUE-ключу — для check'а наличия конкретной роли."""
    stmt = select(RoleACL).where(
        RoleACL.cred_id == cred_id,
        RoleACL.dept_id == dept_id,
        RoleACL.role_name == role_name,
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def create(db: AsyncSession, **fields) -> RoleACL:
    """INSERT нового ACL. commit — на caller'е."""
    obj = RoleACL(**fields)
    db.add(obj)
    await db.flush()
    return obj


async def update_flags(
    db: AsyncSession, acl: RoleACL, *, can_read: bool, can_write: bool
) -> RoleACL:
    """Переписать пару флагов существующего ACL. commit — на caller'е."""
    acl.can_read = can_read
    acl.can_write = can_write
    await db.flush()
    return acl


async def delete(db: AsyncSession, acl: RoleACL) -> None:
    """DELETE одного ACL."""
    await db.delete(acl)
    await db.flush()


async def delete_for_cred_dept(
    db: AsyncSession, cred_id: str, dept_id: str
) -> int:
    """Снести все ACL'и (cred_id, dept_id) — для cascade'а при revoke DeptGrant.

    Возвращает число удалённых строк (для аудит-details).
    """
    stmt = sa_delete(RoleACL).where(
        RoleACL.cred_id == cred_id, RoleACL.dept_id == dept_id
    )
    result = await db.execute(stmt)
    await db.flush()
    return result.rowcount or 0


async def delete_outside_dept(
    db: AsyncSession, cred_id: str, keep_dept_id: str
) -> int:
    """Снести ACL'и кред'ы, висящие НЕ в `keep_dept_id`.

    Используется при удалении владельца personal-кред'ы: ACL личной кред'ы
    легитимен только в dep'е владельца. Если в БД остались строки в чужих
    dep'ах (наследие прежнего владельца / косяк write-side'а), их надо снести,
    иначе после transfer'а чужой dep сохранит доступ к кред'е нового владельца.

    Возвращает число удалённых строк.
    """
    stmt = sa_delete(RoleACL).where(
        RoleACL.cred_id == cred_id, RoleACL.dept_id != keep_dept_id
    )
    result = await db.execute(stmt)
    await db.flush()
    return result.rowcount or 0
