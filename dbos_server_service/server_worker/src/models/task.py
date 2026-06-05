"""Task ORM-model — persistent история всех исполнений worker'а."""

from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.core.constants import TaskStatus
from src.db.base import Base


class Task(Base):
    """ORM-row для одной task'и worker'а.

    Создаётся server_service'ом при dispatch'е (см. `worker_client`), worker
    обновляет status/attempt/last_error/result через `repositories/task.py`.
    """

    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    # `task_kind` — taskiq broker label (`power.on`, `inventory.sync`, ...).
    # Индексируется для запросов «все power.on за сутки» / health-check'ов.
    # CHECK constraint по `TaskKind` enum'у сознательно не выставлен:
    # каждое новое значение требовало бы alembic-миграцию вдогонку за код-
    # релизом, что замедляет добавление task-handler'а. Опечатка пройдёт в
    # БД, но `broker.find_task` вернёт None и dispatch_outbox запаркует row
    # с reason=unknown_task_kind (см. `tasks/dispatch_outbox.py`) — fail-loud,
    # без silent-drop.
    task_kind: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    # `target_server_id` — основной target. Индекс для UI «история task'ов
    # по серверу». nullable для system.heartbeat и подобных bookkeeping-
    # task'ов без конкретного сервера.
    target_server_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    # `target_resource_id` — зарезервировано под secondary-target (server_account
    # id для password-task'ов, ipmi_controller id и т.д.). На сегодня пишется
    # лишь несколькими handler'ами для UI-фильтрации «история task'ов по
    # аккаунту»; индекс не вешаем сознательно — горячих WHERE-фильтров по
    # этой колонке в коде нет, добавим вместе с UI-эндпоинтом, который
    # реально начнёт его читать.
    target_resource_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=TaskStatus.QUEUED
    )
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Максимум попыток выполнения. Читается _runner.py при decision на retry
    # (current_attempt < max_attempts → mark_pending_for_retry, иначе
    # mark_failed). Дефолт 3 пишется на Python-уровне; server_default не
    # выставлен сознательно, чтобы autogenerate не плодил alter-миграций.
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    enqueued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # idempotency_key UNIQUE — NULL-safe per PostgreSQL (несколько NULL
    # допустимы). Используется server_service'ом для дедупа повторных
    # dispatch'ей по одной и той же кнопке UI.
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # ── Durable retry scheduling ───────────────────────────────────────
    # `_schedule_retry` пишет сюда планируемое время re-kick'а. Если
    # worker умрёт во время `asyncio.sleep(delay)`, перезапущенный увидит
    # `status='queued' AND scheduled_retry_at <= now()` и сделает kiq —
    # иначе был сценарий «навсегда queued».
    scheduled_retry_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # ── Cross-replica zombie watchdog ──────────────────────────────────
    # ID worker-процесса, запустившего impl (`mark_running`). Sweep
    # (`tasks_sweep_orphaned`) находит tasks, чей worker уже не шлёт
    # heartbeat'ы — упал OOM/node-failure'ом до drain'а. NULL = legacy-row
    # или тестовая запись.
    worker_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # ── Operator cancel ────────────────────────────────────────────────
    # Заполняется server_service'ом через cross-DB UPDATE при дёрге
    # `POST /api/server/v1/tasks/{id}/cancel`. Сам status переводится в
    # CANCELLED тем же UPDATE'ом. Worker при подборе следующей попытки
    # видит статус через CAS на `mark_running` и пропускает row; running-
    # task'у это не убивает — graceful, она доживает текущий stage и
    # просто не стартует следующий.
    cancelled_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    cancel_reason: Mapped[str | None] = mapped_column(String(512), nullable=True)

    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_task_idempotency_key"),
        Index("ix_tasks_kind_status", "task_kind", "status"),
        # `(status, enqueued_at)` — для worker startup-scan'а «найди мне
        # все queued, отсортируй по времени постановки» (когда фича оживёт).
        Index("ix_tasks_status_enqueued", "status", "enqueued_at"),
        # Partial index по «есть назначенное время retry'я» — startup
        # recovery (`_recover_scheduled_retries`) сканирует только их,
        # таблица в целом не сканируется.
        Index(
            "ix_tasks_scheduled_retry",
            "scheduled_retry_at",
            postgresql_where=text("scheduled_retry_at IS NOT NULL"),
        ),
    )
