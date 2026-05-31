"""ORM-модель `AuditEvent` — append-only журнал аудита."""

from datetime import datetime, timezone

from sqlalchemy import DateTime, Index, String, Boolean, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base
from src.utils.ids import audit_event_id


class AuditEvent(Base):
    __tablename__ = "audit_events"

    # Неизменяемый ID.
    id: Mapped[str] = mapped_column(String(48), primary_key=True, default=audit_event_id)

    # Когда событие произошло (по словам сервиса-источника).
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    # Когда событие приняло loging_service.
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    # Источник.
    service: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    # Что произошло.
    action: Mapped[str] = mapped_column(String(128), nullable=False, index=True)

    # Кто сделал.
    actor_id: Mapped[str | None] = mapped_column(String(48), nullable=True, index=True)
    actor_type: Mapped[str] = mapped_column(String(32), nullable=False)
    username: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    department_id: Mapped[str | None] = mapped_column(String(48), nullable=True, index=True)

    # Над чем.
    target_id: Mapped[str | None] = mapped_column(String(48), nullable=True)
    target_type: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Исход.
    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    # True если операция авторизована (status != "denied").
    allowed: Mapped[bool] = mapped_column(Boolean, nullable=False, index=True)
    # TRACE | DEBUG | INFO | WARNING | ERROR | CRITICAL.
    severity: Mapped[str] = mapped_column(String(16), nullable=False, index=True, default="INFO")

    # Трассировка.
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Free-form технический контекст — без секретов и паролей.
    details: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    # Опциональный opaque dedup-ключ. Scope `(service, idempotency_key)`
    # через partial UNIQUE — форсится только когда ключ не NULL; legacy /
    # one-shot ingest не задеваются. Два POST'а с одинаковой парой
    # считаются одним логическим событием (outbox-retry дедуп).
    idempotency_key: Mapped[str | None] = mapped_column(
        String(128), nullable=True
    )

    # SHA-256 от canonical-JSON payload'а, заполняется только когда
    # `idempotency_key` задан. На retry с тем же ключом репозиторий
    # сверяет хэш: совпало — idempotent replay (возвращаем существующий
    # row), разошлось — 409 IDEMPOTENCY_KEY_CONFLICT. Без этой колонки
    # ON CONFLICT DO NOTHING молча возвращал бы любой первый row под
    # ключом, что позволяло держателю SERVICE_API_KEY застолбить
    # idempotency_key чужим payload'ом ("idempotency poisoning").
    idempotency_payload_hash: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )

    __table_args__ = (
        # Композитный индекс под самый частый паттерн запроса.
        Index("ix_audit_events_service_timestamp", "service", "timestamp"),
        Index("ix_audit_events_department_timestamp", "department_id", "timestamp"),
        # Partial UNIQUE — см. миграцию `h8c9d0e1f2a3`. ORM-зеркало держит
        # `Base.metadata.create_all` (используется в тестах) в синхронности
        # с Alembic'овым view схемы.
        Index(
            "uq_audit_events_service_idempotency_key",
            "service",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
    )
