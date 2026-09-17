"""StpTestRun-репозиторий — сырой CRUD против таблицы `stp_test_runs`."""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import StpTestRun, TestStand


async def get_by_id(db: AsyncSession, run_id: str) -> StpTestRun | None:
    stmt = select(StpTestRun).where(StpTestRun.id == run_id)
    return (await db.execute(stmt)).scalar_one_or_none()


async def get_by_zephyr_key(db: AsyncSession, zephyr_test_run_key: str) -> StpTestRun | None:
    """Локальный прогон, уже привязанный к этому Zephyr test-run — точка
    идемпотентности `services/stp_pull_from_life.py` (§D8): повторный импорт
    находит существующую строку вместо дубля. `zephyr_test_run_key` не несёт
    UNIQUE на уровне схемы (см. docstring модели), поэтому при неожиданном
    дубле забирается самый свежий."""
    stmt = (
        select(StpTestRun)
        .where(StpTestRun.zephyr_test_run_key == zephyr_test_run_key)
        .order_by(StpTestRun.created_at.desc())
        .limit(1)
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def find_latest_for_context(
    db: AsyncSession, *, stand_id: str, os_version_id: str, mode: str, kernel: str,
) -> StpTestRun | None:
    """Самый свежий СТП-прогон под этот `(stand, RC, mode, kernel)` — используется
    событийным обновлением статуса (`services/stp_status.py`) чтобы найти, в какой
    прогон записать результат завершившегося `queue_item`."""
    stmt = (
        select(StpTestRun)
        .where(
            StpTestRun.stand_id == stand_id,
            StpTestRun.os_version_id == os_version_id,
            StpTestRun.mode == mode,
            StpTestRun.kernel == kernel,
        )
        .order_by(StpTestRun.created_at.desc())
        .limit(1)
    )
    return (await db.execute(stmt)).scalar_one_or_none()


def _apply_filters(
    stmt, *, stand_id: str | None, os_version_id: str | None, department_id: str | None = None,
):
    if stand_id is not None:
        stmt = stmt.where(StpTestRun.stand_id == stand_id)
    if os_version_id is not None:
        stmt = stmt.where(StpTestRun.os_version_id == os_version_id)
    if department_id is not None:
        # Своего department у прогона нет — join на стенд, как в
        # `list_by_department_and_os_version`.
        stmt = stmt.join(TestStand, TestStand.id == StpTestRun.stand_id).where(
            TestStand.department_id == department_id
        )
    return stmt


async def list_all(
    db: AsyncSession,
    limit: int = 100,
    offset: int = 0,
    *,
    stand_id: str | None = None,
    os_version_id: str | None = None,
    department_id: str | None = None,
) -> list[StpTestRun]:
    stmt = _apply_filters(
        select(StpTestRun),
        stand_id=stand_id, os_version_id=os_version_id, department_id=department_id,
    )
    stmt = stmt.order_by(StpTestRun.created_at.desc()).limit(limit).offset(offset)
    return list((await db.execute(stmt)).scalars())


async def count_all(
    db: AsyncSession, *, stand_id: str | None = None, os_version_id: str | None = None,
    department_id: str | None = None,
) -> int:
    stmt = _apply_filters(
        select(func.count(StpTestRun.id)),
        stand_id=stand_id, os_version_id=os_version_id, department_id=department_id,
    )
    return int((await db.execute(stmt)).scalar_one())


async def list_by_department_and_os_version(
    db: AsyncSession, department_id: str, os_version_id: str,
) -> list[StpTestRun]:
    """Все `stp_test_runs` этого РЦ, чей стенд принадлежит `department_id`.

    `stp_test_runs` сам department не хранит (см. docstring модели) — фильтр
    идёт через join на `test_stands.department_id`. Используется публикацией
    СТП-матрицы (`services/stp_matrix.py`) — одна страница на РЦ на отдел
    собирает все режимы/ядра/стенды этого отдела для этого РЦ.
    """
    stmt = (
        select(StpTestRun)
        .join(TestStand, TestStand.id == StpTestRun.stand_id)
        .where(
            StpTestRun.os_version_id == os_version_id,
            TestStand.department_id == department_id,
        )
        .order_by(StpTestRun.mode.asc(), StpTestRun.stand_id.asc())
    )
    return list((await db.execute(stmt)).scalars())


async def create(db: AsyncSession, data: dict) -> StpTestRun:
    obj = StpTestRun(**data)
    db.add(obj)
    await db.flush()
    return obj
