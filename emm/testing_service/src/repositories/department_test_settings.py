"""DepartmentTestSettings-репозиторий — сырой CRUD против одной строки на отдел."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import DepartmentTestSettings


async def get_by_department(db: AsyncSession, department_id: str) -> DepartmentTestSettings | None:
    """SELECT по UNIQUE department_id. None — строки ещё нет, штатный случай."""
    stmt = select(DepartmentTestSettings).where(
        DepartmentTestSettings.department_id == department_id
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def list_auto_generate_department_ids(db: AsyncSession) -> list[str]:
    """department_id'шники отделов с включённым `activity_report_auto_generate`."""
    stmt = select(DepartmentTestSettings.department_id).where(
        DepartmentTestSettings.activity_report_auto_generate.is_(True)
    )
    return list((await db.execute(stmt)).scalars())


async def create(db: AsyncSession, data: dict) -> DepartmentTestSettings:
    """INSERT новой строки. commit — на caller'е."""
    obj = DepartmentTestSettings(**data)
    db.add(obj)
    await db.flush()
    return obj


async def update(
    db: AsyncSession, obj: DepartmentTestSettings, changes: dict,
) -> DepartmentTestSettings:
    """In-place setattr + flush. commit — на caller'е."""
    for key, value in changes.items():
        setattr(obj, key, value)
    await db.flush()
    return obj
