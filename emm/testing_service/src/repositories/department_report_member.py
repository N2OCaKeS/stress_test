"""DepartmentReportMember-репозиторий — сырой CRUD против `department_report_members`."""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import DepartmentReportMember


async def get_by_id(db: AsyncSession, member_id: str) -> DepartmentReportMember | None:
    stmt = select(DepartmentReportMember).where(DepartmentReportMember.id == member_id)
    return (await db.execute(stmt)).scalar_one_or_none()


async def list_by_department(
    db: AsyncSession, department_id: str, *, limit: int = 100, offset: int = 0,
    is_active: bool | None = None,
) -> list[DepartmentReportMember]:
    stmt = select(DepartmentReportMember).where(DepartmentReportMember.department_id == department_id)
    if is_active is not None:
        stmt = stmt.where(DepartmentReportMember.is_active == is_active)
    stmt = stmt.order_by(DepartmentReportMember.display_name.asc()).limit(limit).offset(offset)
    return list((await db.execute(stmt)).scalars())


async def count_by_department(
    db: AsyncSession, department_id: str, *, is_active: bool | None = None,
) -> int:
    stmt = select(func.count(DepartmentReportMember.id)).where(
        DepartmentReportMember.department_id == department_id
    )
    if is_active is not None:
        stmt = stmt.where(DepartmentReportMember.is_active == is_active)
    return int((await db.execute(stmt)).scalar_one())


async def list_active_by_department(db: AsyncSession, department_id: str) -> list[DepartmentReportMember]:
    """Без пагинации — используется сведением отчёта (`services/activity_report.py`)."""
    stmt = (
        select(DepartmentReportMember)
        .where(
            DepartmentReportMember.department_id == department_id,
            DepartmentReportMember.is_active.is_(True),
        )
        .order_by(DepartmentReportMember.display_name.asc())
    )
    return list((await db.execute(stmt)).scalars())


async def create(db: AsyncSession, data: dict) -> DepartmentReportMember:
    obj = DepartmentReportMember(**data)
    db.add(obj)
    await db.flush()
    return obj


async def update(db: AsyncSession, obj: DepartmentReportMember, changes: dict) -> DepartmentReportMember:
    for key, value in changes.items():
        setattr(obj, key, value)
    await db.flush()
    return obj


async def delete(db: AsyncSession, obj: DepartmentReportMember) -> None:
    await db.delete(obj)
    await db.flush()
