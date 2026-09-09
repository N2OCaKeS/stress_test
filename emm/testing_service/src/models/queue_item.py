"""Элемент очереди прогона теста на стенде (§2.4, §5.5 плана миграции).

Очередь сериализована по стенду: пока у стенда есть активный элемент
(`queued`/`preparing`/`ready`/`running`), следующий просто ждёт своей
`position`. Между разными стендами очереди независимы и идут параллельно —
эта независимость и есть вся "сериализация", отдельного лока не заводим.

`launch_context` — снэпшот параметров запуска на момент постановки в очередь,
принимается как есть от вызывающего (без резолва глобальных переменных —
это отдельная забота, не в объёме этой волны). Обязан нести `RC`/`KERNEL`/
`MODE`: ими параметризуется вызов `prepare-for-test` у server_service.

`creds_stash_key` — ссылка на одноразовую запись в Redis с кредами тестового
пользователя (см. `services/creds_stash.py`). Сами креды в БД не попадают —
только пока живут в Redis между callback'ом prepare-for-test и claim'ом
`testing_worker`а.

`stand_id`/`test_id` — настоящие FK: `test_stands`/`test_definitions` живут в
этой же БД (в отличие от `server_id`/`os_version_id`, которые указывают в
БД `server_service` и остаются сырыми id без FK).
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base


class QueueItem(Base):
    """Одна постановка теста в очередь стенда."""

    __tablename__ = "queue_items"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    stand_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("test_stands.id", ondelete="RESTRICT"),
        nullable=False, index=True,
    )
    test_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("test_definitions.id", ondelete="RESTRICT"),
        nullable=False, index=True,
    )
    launch_context: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    state: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_retry: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    retry_of_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("queue_items.id", ondelete="SET NULL"), nullable=True,
    )
    debug_mode: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # id запроса на стороне server_service (202-ответ prepare-for-test) —
    # для сшивки входящего callback'а и наблюдаемости.
    prepare_request_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    # Ссылка на Redis-запись с кредами тестового пользователя — не сами креды.
    creds_stash_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    failed_step: Mapped[str | None] = mapped_column(String(32), nullable=True)
    error: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    # Soft-FK на auth_service identity (`usr_<hex>`/`bot_<hex>`).
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
