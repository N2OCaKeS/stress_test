"""ORM-модель transactional outbox для dispatch'а задач воркеру.

Контекст. `worker_client.dispatch_task` шлёт сообщение в Redis сразу после
commit'а транзакции, изменившей серверную БД. Между commit'ом и publish'ем
есть окно, в которое процесс может умереть: БД уже хранит новое состояние
(например `status='reinstalling'`), а в Redis ничего не легло — задача
теряется, оператор видит зависший статус.

Outbox-таблица закрывает окно. Сервис в той же транзакции, что меняет
доменные таблицы, пишет строку `dispatch_outbox` с payload'ом задачи.
Отдельный publisher (Phase C) периодически читает `dispatched_at IS NULL`,
шлёт в Redis и проставляет `dispatched_at = now()`. Падение между commit'ом
и publish'ем безопасно — следующий тик publisher'а добёт строку.

Семантика колонок:

* `dispatched_at IS NULL` — задача ещё не доставлена в Redis;
* `dispatched_at NOT NULL` — publisher подтвердил, что брокер принял;
* `attempts` — сколько раз publisher пробовал отправить;
* `last_error` — текст последней ошибки publish'а (для диагностики);
* `next_retry_at` — когда снова попробовать (None = можно сразу).
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base


class DispatchOutbox(Base):
    """Одна строка outbox'а — задача, которую нужно опубликовать в Redis."""

    __tablename__ = "dispatch_outbox"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # task_id хранится без индекса — repositories/dispatch_outbox.py делает
    # только INSERT, SELECT по task_id отсутствует. При появлении ops-debug
    # эндпоинта индекс восстанавливается отдельной миграцией.
    task_id: Mapped[str] = mapped_column(String(64), nullable=False)
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
