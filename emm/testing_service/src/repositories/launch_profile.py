"""LaunchProfile-репозиторий — сырой CRUD против `launch_profiles`/`launch_profile_versions`."""

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import LaunchProfile, LaunchProfileVersion


async def get_by_id(db: AsyncSession, profile_id: str) -> LaunchProfile | None:
    return await db.get(LaunchProfile, profile_id)


async def list_visible(db: AsyncSession, department_id: str | None) -> list[LaunchProfile]:
    """Профили отдела и общие (`department_id IS NULL`)."""
    stmt = select(LaunchProfile).where(
        or_(LaunchProfile.department_id.is_(None), LaunchProfile.department_id == department_id),
    ).order_by(LaunchProfile.department_id.is_(None), LaunchProfile.name)
    return list((await db.execute(stmt)).scalars().all())


async def get_default(db: AsyncSession, department_id: str | None) -> LaunchProfile | None:
    column = LaunchProfile.department_id
    cond = column.is_(None) if department_id is None else column == department_id
    stmt = select(LaunchProfile).where(cond, LaunchProfile.is_default.is_(True))
    return (await db.execute(stmt)).scalar_one_or_none()


async def clear_default(db: AsyncSession, department_id: str | None, *, except_id: str) -> None:
    column = LaunchProfile.department_id
    cond = column.is_(None) if department_id is None else column == department_id
    await db.execute(
        update(LaunchProfile).where(cond, LaunchProfile.id != except_id).values(is_default=False)
    )


async def create(db: AsyncSession, data: dict) -> LaunchProfile:
    obj = LaunchProfile(**data)
    db.add(obj)
    await db.flush()
    return obj


async def get_version(db: AsyncSession, version_id: str) -> LaunchProfileVersion | None:
    return await db.get(LaunchProfileVersion, version_id)


async def list_versions(db: AsyncSession, profile_id: str) -> list[LaunchProfileVersion]:
    stmt = (
        select(LaunchProfileVersion)
        .where(LaunchProfileVersion.profile_id == profile_id)
        .order_by(LaunchProfileVersion.version.desc())
    )
    return list((await db.execute(stmt)).scalars().all())


async def next_version_number(db: AsyncSession, profile_id: str) -> int:
    stmt = select(func.coalesce(func.max(LaunchProfileVersion.version), 0)).where(
        LaunchProfileVersion.profile_id == profile_id,
    )
    return int((await db.execute(stmt)).scalar_one()) + 1


async def create_version(db: AsyncSession, data: dict) -> LaunchProfileVersion:
    obj = LaunchProfileVersion(**data)
    db.add(obj)
    await db.flush()
    return obj
