"""AuditEvent repository — insert only, never update or delete."""

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.models.audit_event import AuditEvent
from src.schemas.events import EventCreate
from src.utils.ids import audit_event_id


def insert(db: Session, payload: EventCreate) -> AuditEvent:
    event = AuditEvent(
        id=audit_event_id(),
        timestamp=payload.timestamp,
        received_at=datetime.now(timezone.utc),
        service=payload.service,
        action=payload.action,
        actor_id=payload.actor_id,
        actor_type=payload.actor_type,
        username=payload.username,
        department_id=payload.department_id,
        target_id=payload.target_id,
        target_type=payload.target_type,
        status=payload.status,
        allowed=payload.allowed,
        severity=payload.severity,
        request_id=payload.request_id,
        details=payload.details,
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return event


def query(
    db: Session,
    *,
    department_id: str | None = None,
    service: str | None = None,
    severity: str | None = None,
    action: str | None = None,
    from_time: datetime | None = None,
    to_time: datetime | None = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[AuditEvent], int]:
    stmt = select(AuditEvent)
    count_stmt = select(func.count()).select_from(AuditEvent)

    filters = []
    if department_id is not None:
        filters.append(AuditEvent.department_id == department_id)
    if service is not None:
        filters.append(AuditEvent.service == service)
    if severity is not None:
        filters.append(AuditEvent.severity == severity)
    if action is not None:
        filters.append(AuditEvent.action == action)
    if from_time is not None:
        filters.append(AuditEvent.timestamp >= from_time)
    if to_time is not None:
        filters.append(AuditEvent.timestamp <= to_time)

    for f in filters:
        stmt = stmt.where(f)
        count_stmt = count_stmt.where(f)

    total = db.execute(count_stmt).scalar_one()
    events = db.execute(
        stmt.order_by(AuditEvent.timestamp.desc()).offset(offset).limit(limit)
    ).scalars().all()

    return list(events), total


def list_services(db: Session) -> list:
    stmt = (
        select(
            AuditEvent.service,
            func.count(AuditEvent.id).label("event_count"),
            func.max(AuditEvent.timestamp).label("last_event_at"),
        )
        .group_by(AuditEvent.service)
        .order_by(AuditEvent.service)
    )
    return db.execute(stmt).all()
