"""AuditOutbox — durable outbox для audit-событий testing_service.

Назначение то же, что у одноимённой таблицы в `server_worker`: `emit()`
раньше сам стрелял HTTP'ом в loging_service и глушил любую ошибку в
WARNING. Недоступный (или просто перезапускающийся) loging_service молча
съедал событие, включая CRITICAL `permission.grant`/`permission.revoke`.

Путь события теперь:

  1. `services/audit_service.emit()` собирает payload и складывает его в
     staging-буфер запроса (`services/audit_outbox.stage`);
  2. по завершении запроса буфер одним INSERT'ом уезжает в `audit_outbox`
     (`services/audit_outbox.flush_scope`) — с этого момента событие
     durable и переживает падение процесса и недоступность loging;
  3. фоновый drain-loop (`services/audit_outbox_publisher`) выбирает
     unpublished-строки и шлёт их в loging_service;
  4. успех — `published_at = now()`, сбой — `attempts++`, `last_error`,
     `next_retry_at = now() + 2^attempts` (capped), строка остаётся в
     очереди.

Гарантия — at-least-once: дубликаты возможны (например, процесс умер
между реальным POST'ом и commit'ом отметки) и разруливаются на стороне
loging_service по `request_id`/`action`/`timestamp`.

Cap по попыткам (`AUDIT_OUTBOX_MAX_PUBLISH_ATTEMPTS`, дефолт 50) уводит
строку в DLQ: `published_at = now()` + `last_error` с префиксом
`[DLQ:<reason>]`. Причины — `attempts_cap`, `permanent_4xx` (loging
ответил 4xx, payload retry'ями не починить), `missing_action` (битый
payload). Строка перестаёт крутиться в выборке, оператор ловит её по
ERROR-логу и по `last_error` в таблице.
"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, Integer, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base


class AuditOutbox(Base):
    """Одно отложенное audit-событие."""

    __tablename__ = "audit_outbox"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # Дубль `payload["action"]` отдельной колонкой — чтобы оператор видел
    # застрявшее событие обычным SELECT'ом, без раскопок JSONB.
    action: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    # Полностью готовый payload POST /api/logging/v1/events. Redaction и
    # заполнение из audit_context уже сделаны в `audit_service.emit`.
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0"), default=0,
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Per-row backoff: после неудачной попытки publisher ставит сюда
    # `now() + 2^attempts` секунд. Выборка пропускает строки, у которых
    # время ещё не наступило, — иначе при долгой недоступности loging одна
    # строка съедала бы весь batch на каждом тике.
    next_retry_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )

    __table_args__ = (
        # Partial-index только по неопубликованным: drain сканирует их,
        # опубликованные лежат мёртвым грузом до cleanup'а. Ключ
        # `(next_retry_at, created_at)` — фильтр «можно ретраить» сразу
        # попадает в b-tree.
        Index(
            "ix_audit_outbox_unpublished_retry",
            "next_retry_at",
            "created_at",
            postgresql_where=text("published_at IS NULL"),
        ),
    )
