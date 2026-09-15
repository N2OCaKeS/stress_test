"""QueueItem-репозиторий — сырой CRUD + очередные выборки против `queue_items`."""

from datetime import datetime

from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import aliased
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.constants import ACTIVE_QUEUE_STATES, QueueItemState
from src.models import QueueItem, TestStand, TestDefinition


async def get_by_id(db: AsyncSession, item_id: str) -> QueueItem | None:
    """SELECT по PK."""
    stmt = select(QueueItem).where(QueueItem.id == item_id)
    return (await db.execute(stmt)).scalar_one_or_none()


async def get_by_id_for_update(db: AsyncSession, item_id: str) -> QueueItem | None:
    """SELECT ... FOR UPDATE по PK — сериализует конкурентные callback/complete на один item."""
    stmt = select(QueueItem).where(QueueItem.id == item_id).with_for_update().execution_options(populate_existing=True)
    return (await db.execute(stmt)).scalar_one_or_none()


async def get_by_prepare_request_id(db: AsyncSession, prepare_request_id: str) -> QueueItem | None:
    """SELECT по `prepare_request_id` — сшивка входящего callback'а server_service."""
    stmt = select(QueueItem).where(QueueItem.prepare_request_id == prepare_request_id).with_for_update().execution_options(populate_existing=True)
    return (await db.execute(stmt)).scalar_one_or_none()


async def count_active_for_stand(db: AsyncSession, stand_id: str) -> int:
    """Сколько элементов очереди этого стенда сейчас "заняты" (не терминальны).

    Используется, чтобы понять, был ли стенд пуст ДО вставки нового item'а —
    если да, `enqueue` сразу запускает цикл подготовки (см. `services/queue.py`).
    """
    stmt = select(func.count(QueueItem.id)).where(
        QueueItem.stand_id == stand_id,
        QueueItem.state.in_(ACTIVE_QUEUE_STATES),
    )
    return int((await db.execute(stmt)).scalar_one())


async def next_position_for_stand(db: AsyncSession, stand_id: str) -> int:
    """`max(position)+1` среди активных элементов стенда, либо 0 если очередь пуста."""
    stmt = select(func.max(QueueItem.position)).where(
        QueueItem.stand_id == stand_id,
        QueueItem.state.in_(ACTIVE_QUEUE_STATES),
    )
    current_max = (await db.execute(stmt)).scalar_one_or_none()
    return 0 if current_max is None else current_max + 1


async def get_active_for_stand(db: AsyncSession, stand_id: str) -> QueueItem | None:
    """Активный (не терминальный) item этого стенда, если есть.

    Один активный item на стенд — инвариант, который держит `services/queue.py`
    на этапе постановки в очередь, поэтому `limit(1)` здесь не выбирает между
    несколькими кандидатами, а просто закрывает контракт функции. Используется
    консолью сервера (§8.6 плана миграции) — сигнал показать кнопку
    «Живой лог теста».
    """
    stmt = (
        select(QueueItem)
        .where(QueueItem.stand_id == stand_id, QueueItem.state.in_(ACTIVE_QUEUE_STATES))
        .order_by(QueueItem.created_at.desc())
        .limit(1)
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def get_next_queued_for_stand(db: AsyncSession, stand_id: str) -> QueueItem | None:
    """Следующий `queued`-item этого стенда (наименьший `position`) — для продолжения очереди."""
    stmt = (
        select(QueueItem)
        .where(QueueItem.stand_id == stand_id, QueueItem.state == QueueItemState.QUEUED)
        .order_by(QueueItem.position.asc(), QueueItem.created_at.asc())
        .limit(1)
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def claim_next_ready(db: AsyncSession) -> QueueItem | None:
    """Атомарно забрать один `ready`-item по любому стенду.

    `SKIP LOCKED` — конкурентные вызовы `claim` разных воркеров не блокируют
    друг друга на чужих строках и не могут получить один и тот же item дважды.
    Сериализация "один активный item на стенд" гарантирована на этапе
    постановки в очередь (`services/queue.py`), поэтому по стендам здесь
    выбирать не нужно — `ready`-строк одновременно ровно столько, сколько
    стендов завершили подготовку.
    """
    stmt = (
        select(QueueItem)
        .where(QueueItem.state == QueueItemState.READY)
        .order_by(QueueItem.position.asc(), QueueItem.created_at.asc())
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def list_by_test_run_id(db: AsyncSession, test_run_id: str) -> list[QueueItem]:
    """Все item'ы, порождённые этой кампанией — для агрегации статуса и детальной карточки."""
    stmt = (
        select(QueueItem)
        .where(QueueItem.test_run_id == test_run_id)
        .order_by(QueueItem.created_at.asc())
    )
    return list((await db.execute(stmt)).scalars())


async def create(db: AsyncSession, data: dict) -> QueueItem:
    """INSERT новой строки. commit — на caller'е."""
    obj = QueueItem(**data)
    db.add(obj)
    await db.flush()
    return obj


async def update(db: AsyncSession, obj: QueueItem, changes: dict) -> QueueItem:
    """In-place setattr + flush. commit — на caller'е."""
    for key, value in changes.items():
        setattr(obj, key, value)
    await db.flush()
    return obj


async def lock_request(db: AsyncSession, actor: str, request_id: str) -> None:
    await db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": f"queue:{actor}:{request_id}"})


async def find_request(db: AsyncSession, actor: str, request_id: str) -> QueueItem | None:
    stmt = select(QueueItem).where(QueueItem.created_by == actor, QueueItem.client_request_id == request_id)
    return (await db.execute(stmt)).scalar_one_or_none()


async def has_successor(db: AsyncSession, item_id: str) -> bool:
    stmt = select(QueueItem.id).where(QueueItem.retry_of_id == item_id).limit(1)
    return (await db.execute(stmt)).scalar_one_or_none() is not None


async def aggregate_pool_overview(
    db: AsyncSession, department_id: str, *, kind: str,
    test_run_id: str | None = None,
    created_from: datetime | None = None, created_until: datetime | None = None,
) -> dict[str, int]:
    """Считает очередь/исходы обзора пула (§F плана 2026-09-11) одним запросом.

    `remaining`/`running` не фильтруются по "последней попытке" — только
    терминальный item может быть перекрыт retry'ем (см. контракт §B1), значит
    активный (`queued`/`preparing`/`ready`/`running`) всегда и есть последняя
    попытка своей цепочки. `succeeded`/`failed` — наоборот, только по
    последней попытке (`is_current`), иначе перезапущенный упавший тест
    заодно посчитался бы дважды.
    """
    successor = aliased(QueueItem)
    is_current = ~select(successor.id).where(successor.retry_of_id == QueueItem.id).exists()
    stmt = (
        select(QueueItem.state, is_current)
        .join(TestStand, TestStand.id == QueueItem.stand_id)
        .where(TestStand.department_id == department_id)
    )
    if kind == "standalone":
        stmt = stmt.where(QueueItem.test_run_id.is_(None))
    elif kind == "campaign":
        stmt = stmt.where(QueueItem.test_run_id.is_not(None))
    if test_run_id is not None:
        stmt = stmt.where(QueueItem.test_run_id == test_run_id)
    if created_from is not None:
        stmt = stmt.where(QueueItem.created_at >= created_from)
    if created_until is not None:
        stmt = stmt.where(QueueItem.created_at < created_until)

    remaining = 0
    running = 0
    succeeded = 0
    failed = 0
    for state, current in await db.execute(stmt):
        if state in (QueueItemState.QUEUED, QueueItemState.PREPARING, QueueItemState.READY):
            remaining += 1
        elif state == QueueItemState.RUNNING:
            running += 1
        elif current and state == QueueItemState.SUCCEEDED:
            succeeded += 1
        elif current and state == QueueItemState.FAILED:
            failed += 1
    return {"remaining": remaining, "running": running, "succeeded": succeeded, "failed": failed}


async def list_for_department(
    db: AsyncSession, department_id: str, *, kind: str,
    test_run_id: str | None, limit: int, offset: int,
    os_version_id: str | None = None, os_version_name: str | None = None, kernel: str | None = None,
    stand_id: str | None = None, test_id: str | None = None,
    attempt_id: str | None = None, retry_of_id: str | None = None,
    created_from: datetime | None = None, created_until: datetime | None = None,
    states: list[str] | None = None, debug_mode: bool | None = None,
    q: str | None = None, order: str = "desc",
):
    successor = aliased(QueueItem)
    is_current = ~select(successor.id).where(successor.retry_of_id == QueueItem.id).exists()
    stmt = (
        select(QueueItem, TestDefinition.code, TestDefinition.full_name, is_current)
        .join(TestStand, TestStand.id == QueueItem.stand_id)
        .join(TestDefinition, TestDefinition.id == QueueItem.test_id)
        .where(TestStand.department_id == department_id)
    )
    if kind == "standalone":
        stmt = stmt.where(QueueItem.test_run_id.is_(None))
    elif kind == "campaign":
        stmt = stmt.where(QueueItem.test_run_id.is_not(None))
    for column, value in (
        (QueueItem.test_run_id, test_run_id), (QueueItem.stand_id, stand_id),
        (QueueItem.test_id, test_id), (QueueItem.id, attempt_id),
        (QueueItem.retry_of_id, retry_of_id), (QueueItem.debug_mode, debug_mode),
    ):
        if value is not None:
            stmt = stmt.where(column == value)
    if os_version_id is not None:
        aliases = [os_version_id] + ([os_version_name] if os_version_name else [])
        stmt = stmt.where(QueueItem.launch_context["RC"].astext.in_(aliases))
    if kernel is not None:
        stmt = stmt.where(QueueItem.launch_context["KERNEL"].astext == kernel)
    if created_from is not None:
        stmt = stmt.where(QueueItem.created_at >= created_from)
    if created_until is not None:
        stmt = stmt.where(QueueItem.created_at < created_until)
    if states:
        stmt = stmt.where(QueueItem.state.in_(states))
    if q and q.strip():
        stmt = stmt.where(or_(*[
            column.icontains(q.strip(), autoescape=True) for column in (
                QueueItem.id, TestDefinition.code, TestDefinition.full_name,
                QueueItem.stand_id, TestStand.server_id,
            )
        ]))
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    sort = QueueItem.created_at.asc() if order == "asc" else QueueItem.created_at.desc()
    items = (await db.execute(stmt.order_by(sort, QueueItem.id).limit(limit).offset(offset))).all()
    return items, total
