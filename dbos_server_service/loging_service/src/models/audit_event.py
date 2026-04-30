"""AuditEvent ORM model — append-only audit log."""

from datetime import datetime, timezone

from sqlalchemy import DateTime, Index, String, Boolean, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base
from src.utils.ids import audit_event_id


class AuditEvent(Base):
    __tablename__ = "audit_events"

    # Immutable identity
    id: Mapped[str] = mapped_column(String(48), primary_key=True, default=audit_event_id)

    # When the originating service says the event occurred
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    # When logging_service received the event
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    # Source
    service: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    # What happened
    action: Mapped[str] = mapped_column(String(128), nullable=False, index=True)

    # Who did it
    actor_id: Mapped[str | None] = mapped_column(String(48), nullable=True, index=True)
    actor_type: Mapped[str] = mapped_column(String(32), nullable=False)
    username: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    department_id: Mapped[str | None] = mapped_column(String(48), nullable=True, index=True)

    # What it was done to
    target_id: Mapped[str | None] = mapped_column(String(48), nullable=True)
    target_type: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Outcome
    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    # True when the operation was authorised (status != "denied")
    allowed: Mapped[bool] = mapped_column(Boolean, nullable=False, index=True)
    # TRACE | DEBUG | INFO | WARNING | ERROR | CRITICAL
    severity: Mapped[str] = mapped_column(String(16), nullable=False, index=True, default="INFO")

    # Tracing
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Free-form technical context — no secrets, no passwords
    details: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    __table_args__ = (
        # Composite index for the most common query pattern
        Index("ix_audit_events_service_timestamp", "service", "timestamp"),
        Index("ix_audit_events_department_timestamp", "department_id", "timestamp"),
    )
