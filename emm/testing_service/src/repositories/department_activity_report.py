"""DepartmentActivityReport-репозиторий — история генераций HR-отчёта."""

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import DepartmentActivityReport


async def get_by_id(db: AsyncSession, report_id: str) -> DepartmentActivityReport | None:
    stmt = select(DepartmentActivityReport).where(DepartmentActivityReport.id == report_id)
    return (await db.execute(stmt)).scalar_one_or_none()


async def list_by_department(
    db: AsyncSession, department_id: str, *, limit: int = 100, offset: int = 0,
) -> list[DepartmentActivityReport]:
    stmt = (
        select(DepartmentActivityReport)
        .where(DepartmentActivityReport.department_id == department_id)
        .order_by(desc(DepartmentActivityReport.generated_at))
        .limit(limit)
        .offset(offset)
    )
    return list((await db.execute(stmt)).scalars())


async def count_by_department(db: AsyncSession, department_id: str) -> int:
    stmt = select(func.count(DepartmentActivityReport.id)).where(
        DepartmentActivityReport.department_id == department_id
    )
    return int((await db.execute(stmt)).scalar_one())


async def create(db: AsyncSession, data: dict) -> DepartmentActivityReport:
    obj = DepartmentActivityReport(**data)
    db.add(obj)
    await db.flush()
    return obj


async def update(db: AsyncSession, obj: DepartmentActivityReport, changes: dict) -> DepartmentActivityReport:
    for key, value in changes.items():
        setattr(obj, key, value)
    await db.flush()
    return obj
