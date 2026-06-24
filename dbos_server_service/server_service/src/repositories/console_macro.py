"""ConsoleMacro-репозиторий — сырой CRUD против таблицы `console_macros`."""

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import ConsoleMacro


async def get_by_id(db: AsyncSession, macro_id: str) -> ConsoleMacro | None:
    """SELECT по PK."""
    stmt = select(ConsoleMacro).where(ConsoleMacro.id == macro_id)
    return (await db.execute(stmt)).scalar_one_or_none()


async def list_for_user(
    db: AsyncSession,
    *,
    user_id: str,
    department_id: str | None,
) -> list[ConsoleMacro]:
    """Личные макросы caller'а + системные его отдела.

    Сортировка `(is_system, display_order, id)` — личные идут раньше системных,
    внутри группы по display_order; tie-break по id для стабильного порядка.
    """
    visibility = ConsoleMacro.user_id == user_id
    if department_id is not None:
        visibility = visibility | and_(
            ConsoleMacro.is_system.is_(True),
            ConsoleMacro.department_id == department_id,
        )
    stmt = (
        select(ConsoleMacro)
        .where(visibility)
        .order_by(
            ConsoleMacro.is_system,
            ConsoleMacro.display_order,
            ConsoleMacro.id,
        )
    )
    return list((await db.execute(stmt)).scalars())


async def create(db: AsyncSession, data: dict) -> ConsoleMacro:
    """INSERT новой строки. commit — на caller'е."""
    obj = ConsoleMacro(**data)
    db.add(obj)
    await db.flush()
    return obj


async def update(db: AsyncSession, obj: ConsoleMacro, changes: dict) -> ConsoleMacro:
    """In-place setattr + flush. commit — на caller'е."""
    for key, value in changes.items():
        setattr(obj, key, value)
    await db.flush()
    return obj


async def delete(db: AsyncSession, obj: ConsoleMacro) -> None:
    """DELETE объекта. commit — на caller'е."""
    await db.delete(obj)
    await db.flush()
