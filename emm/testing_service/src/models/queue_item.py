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

`test_run_id` — NULL для одиночных `enqueue()`-вызовов вне кампании (как это
работает сегодня), заполнено, когда item порождён `test_run` (§2.4, §6.1).
`ON DELETE SET NULL`, не `RESTRICT`/`CASCADE`: удаления `test_runs` в сервисе
пока нет, но если оно появится, история отдельных item'ов очереди не должна
схлопываться каскадом вместе с кампанией, которая её породила.
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base


class QueueItem(Base):
    """Одна постановка теста в очередь стенда."""

    __tablename__ = "queue_items"
    __table_args__ = (UniqueConstraint("created_by", "client_request_id", name="uq_queue_items_client_request"),)

    log_rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    client_request_id: Mapped[str | None] = mapped_column(String(128))
    request_fingerprint: Mapped[str | None] = mapped_column(String(64))
    stp_test_run_id: Mapped[str | None] = mapped_column(String(64))

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
    # Флаг режима «только подготовить стенд» (легаси testenv_*.conf). Вместо
    # запуска run.py воркер кладёт на стенд testenv-маркер и command.txt с
    # тем, что было бы запущено, и item уходит в свой терминальный статус
    # (`prepared`), не в обычный succeeded/failed — см. services/queue.py.
    prepare_only: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    test_run_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("test_runs.id", ondelete="SET NULL"), nullable=True, index=True,
    )
    test_run_entry_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("test_run_entries.id", ondelete="SET NULL"), nullable=True, index=True,
    )
    # id запроса на стороне server_service (202-ответ prepare-for-test) —
    # для сшивки входящего callback'а и наблюдаемости.
    prepare_request_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    # Ссылка на Redis-запись с кредами тестового пользователя — не сами креды.
    creds_stash_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Заявка на прерывание уже исполняющегося элемента (`skip`/`pause`) —
    # читается воркером через `/internal/queue/{id}/interrupt-check` и
    # сбрасывается, когда он отчитался о прерывании.
    interrupt_action: Mapped[str | None] = mapped_column(String(16), nullable=True)
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
