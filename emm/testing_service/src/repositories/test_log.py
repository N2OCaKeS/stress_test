"""TestLog-репозиторий — CRUD + выборки для ротации (§2.6, §8.5 плана миграции)."""

from datetime import datetime

from sqlalchemy import or_, select, update as sa_update
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import TestLog


async def get_by_id(db: AsyncSession, log_id: str) -> TestLog | None:
    """SELECT по PK."""
    stmt = select(TestLog).where(TestLog.id == log_id)
    return (await db.execute(stmt)).scalar_one_or_none()


async def get_by_queue_item_id(db: AsyncSession, queue_item_id: str) -> TestLog | None:
    """SELECT по UNIQUE queue_item_id — read-путь (эндпоинты чтения)."""
    stmt = select(TestLog).where(TestLog.queue_item_id == queue_item_id)
    return (await db.execute(stmt)).scalar_one_or_none()


async def get_by_queue_item_id_for_update(db: AsyncSession, queue_item_id: str) -> TestLog | None:
    """SELECT ... FOR UPDATE по queue_item_id.

    Сериализует конкурентные append'ы одного лога (в т.ч. лениво создающие
    его первым чанком/сегментом, см. `services/test_log.py::get_or_create_log`)
    — на этой строке и держится вся блокировка записи, отдельно блоб не
    лочится.
    """
    stmt = select(TestLog).where(TestLog.queue_item_id == queue_item_id).with_for_update()
    return (await db.execute(stmt)).scalar_one_or_none()


async def create(db: AsyncSession, data: dict) -> TestLog:
    """INSERT новой строки. commit — на caller'е."""
    obj = TestLog(**data)
    db.add(obj)
    await db.flush()
    return obj


async def delete(db: AsyncSession, obj: TestLog) -> None:
    """DELETE объекта — каскадно сносит его сегменты и блоб (FK ON DELETE CASCADE)."""
    await db.delete(obj)
    await db.flush()


async def list_distinct_branches(db: AsyncSession) -> list[str]:
    """Уникальные значения `os_version_major`, встречающиеся хоть у одного лога."""
    stmt = select(TestLog.os_version_major).where(TestLog.os_version_major.is_not(None)).distinct()
    return [row[0] for row in (await db.execute(stmt)).all()]


async def list_distinct_rc_by_latest(db: AsyncSession, os_version_major: str) -> list[tuple[str, datetime]]:
    """Уникальные `rc` этой ветки, отсортированные по максимальному `created_at` убыв.

    Первые два элемента результата — те `rc`, что должны остаться `protected`
    (см. `services/log_rotation.py::recompute_protection_for_branch`).
    """
    stmt = (
        select(TestLog.rc, func.max(TestLog.created_at).label("latest"))
        .where(TestLog.os_version_major == os_version_major, TestLog.rc.is_not(None))
        .group_by(TestLog.rc)
        .order_by(func.max(TestLog.created_at).desc())
    )
    return [(row[0], row[1]) for row in (await db.execute(stmt)).all()]


async def set_protected_for_branch(db: AsyncSession, os_version_major: str, protected_rcs: set[str]) -> None:
    """Проставить `protected` всем логам ветки по членству `rc` в `protected_rcs`.

    Логи без `rc` (NULL) в "топ-2" попасть не могут — всегда `protected=false`.
    """
    if protected_rcs:
        await db.execute(
            sa_update(TestLog)
            .where(TestLog.os_version_major == os_version_major, TestLog.rc.in_(protected_rcs))
            .values(protected=True)
        )
        await db.execute(
            sa_update(TestLog)
            .where(
                TestLog.os_version_major == os_version_major,
                or_(TestLog.rc.not_in(protected_rcs), TestLog.rc.is_(None)),
            )
            .values(protected=False)
        )
    else:
        await db.execute(
            sa_update(TestLog).where(TestLog.os_version_major == os_version_major).values(protected=False)
        )
    await db.flush()


async def find_unprotected_duplicates(db: AsyncSession, *, test_id: str, rc: str, kernel: str) -> list[TestLog]:
    """Незащищённые логи с тем же `(test_id, rc, kernel)` — кандидаты немедленной ротации."""
    stmt = select(TestLog).where(
        TestLog.test_id == test_id, TestLog.rc == rc, TestLog.kernel == kernel,
        TestLog.protected.is_(False),
    )
    return list((await db.execute(stmt)).scalars())


async def find_stale_unprotected(db: AsyncSession, cutoff: datetime) -> list[TestLog]:
    """Незащищённые логи старше `cutoff` — кандидаты ежемесячной чистки."""
    stmt = select(TestLog).where(TestLog.protected.is_(False), TestLog.created_at < cutoff)
    return list((await db.execute(stmt)).scalars())
