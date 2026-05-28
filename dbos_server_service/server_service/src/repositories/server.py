"""Server-репозиторий — сырой CRUD против таблицы `servers`."""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import Server


async def list_in_departments(
    db: AsyncSession,
    department_ids: list[str] | None,
    limit: int = 100,
    offset: int = 0,
) -> list[Server]:
    """SELECT серверов, опционально ограниченный набором отделов.

    `department_ids=None` → все сервера (зарезервировано под будущие
    operator-сценарии; service-layer сейчас всегда передаёт явный список).
    `department_ids=[]` → пусто (caller без department).
    """
    stmt = select(Server).order_by(Server.created_at.desc()).limit(limit).offset(offset)
    if department_ids is not None:
        if not department_ids:
            return []
        stmt = stmt.where(Server.department_id.in_(department_ids))
    return list((await db.execute(stmt)).scalars())


async def count_in_departments(
    db: AsyncSession,
    department_ids: list[str] | None,
) -> int:
    """COUNT под тем же фильтром, что и list — для total в pagination."""
    stmt = select(func.count(Server.id))
    if department_ids is not None:
        if not department_ids:
            return 0
        stmt = stmt.where(Server.department_id.in_(department_ids))
    return int((await db.execute(stmt)).scalar_one())


async def get_by_id(db: AsyncSession, server_id: str) -> Server | None:
    """SELECT по PK. Visibility-check делает service-layer."""
    stmt = select(Server).where(Server.id == server_id)
    return (await db.execute(stmt)).scalar_one_or_none()


async def get_many_by_ids(
    db: AsyncSession, server_ids: list[str]
) -> dict[str, Server]:
    """SELECT всех серверов из списка одним `WHERE id IN (...)` запросом.

    Возвращает словарь по id (несуществующие просто отсутствуют). Для массовых
    fan-out'ов (ротация, update_on_host), где иначе на каждый server_id шёл
    бы свой round-trip в БД.
    """
    if not server_ids:
        return {}
    stmt = select(Server).where(Server.id.in_(server_ids))
    rows = list((await db.execute(stmt)).scalars())
    return {srv.id: srv for srv in rows}


async def get_by_hostname(db: AsyncSession, hostname: str) -> Server | None:
    """SELECT по hostname (UNIQUE). Используется опционально под дедупликацию."""
    stmt = select(Server).where(Server.hostname == hostname)
    return (await db.execute(stmt)).scalar_one_or_none()


async def create(db: AsyncSession, data: dict) -> Server:
    """INSERT новой строки. commit делает caller."""
    obj = Server(**data)
    db.add(obj)
    await db.flush()
    return obj


async def update(db: AsyncSession, obj: Server, changes: dict) -> Server:
    """In-place setattr по словарю изменений + flush. commit — на caller'е."""
    for key, value in changes.items():
        setattr(obj, key, value)
    await db.flush()
    return obj


async def delete(db: AsyncSession, obj: Server) -> None:
    """DELETE объекта. Каскад на child-таблицы — через ondelete=CASCADE."""
    await db.delete(obj)
    await db.flush()
