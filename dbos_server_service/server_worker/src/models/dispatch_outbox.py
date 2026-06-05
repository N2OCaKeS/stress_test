"""Зеркало `DispatchOutbox` из server_service для publisher'а в worker'е.

Сама таблица `dispatch_outbox` живёт в БД server_service (миграция
`e7a4d951c3b2_dispatch_outbox`). Worker читает её через отдельный engine
(см. `db/dispatch_outbox_session.py`) — поэтому модель определена под
**отдельным** DeclarativeBase, чтобы её таблица не попала в `Base.metadata`
основного engine'а и Alembic worker'а не пытался её автогенерировать.

Назначение outbox — закрыть окно потери между commit'ом доменной транзакции
в server_service и публикацией задачи в Redis (`broker.kick`). Server_service
в той же транзакции с UPDATE доменной таблицы пишет строку в `dispatch_outbox`,
этот publisher периодически читает `dispatched_at IS NULL` и шлёт в broker.

Колонки 1:1 повторяют server_service-копию; см. docstring оригинала
(`dbos_server_service/server_service/src/models/dispatch_outbox.py`).
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Index, Integer, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class DispatchOutboxBase(DeclarativeBase):
    """Отдельный MetaData для cross-DB модели.

    Worker-Alembic читает `src.db.base.Base.metadata` и автогенерит миграции
    под worker-БД. `dispatch_outbox` живёт в server_service-БД, мигрируется
    оттуда — поэтому здесь свой Base, чтобы worker-Alembic её не подбирал.
    """


class DispatchOutbox(DispatchOutboxBase):
    """Read/UPDATE-only view на таблицу из server_service-БД."""

    __tablename__ = "dispatch_outbox"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    task_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    task_kind: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    dispatched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_retry_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # ORM-mirror индексов, реально создаваемых миграцией
    # `server_service/.../e7a4d951c3b2_dispatch_outbox.py`. Worker-Alembic
    # эту модель НЕ подбирает (отдельный `DispatchOutboxBase`-metadata), но
    # читатель кода видит здесь те же partial-индексы, что и в server_service,
    # — нет drift'а между «как ORM описывает» и «что в БД». Если cross-DB
    # source-of-truth когда-то переедет в worker, эти строки уже зафиксируют
    # ожидаемые pending/retry-фильтры.
    __table_args__ = (
        Index(
            "ix_dispatch_outbox_pending",
            "created_at",
            postgresql_where=text("dispatched_at IS NULL"),
        ),
        Index(
            "ix_dispatch_outbox_retry",
            "next_retry_at",
            postgresql_where=text(
                "dispatched_at IS NULL AND next_retry_at IS NOT NULL"
            ),
        ),
    )
