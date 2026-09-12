"""QueueItem-репозиторий — сырой CRUD + очередные выборки против `queue_items`."""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.constants import ACTIVE_QUEUE_STATES, QueueItemState
from src.models import QueueItem


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
    stmt = select(QueueItem).where(QueueItem.prepare_request_id == prepare_request_id)
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
