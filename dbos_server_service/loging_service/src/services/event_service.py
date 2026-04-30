"""Business logic for audit event ingestion and querying."""

from datetime import datetime

from sqlalchemy.orm import Session

from src.models.audit_event import AuditEvent
from src.repositories import events as event_repo
from src.schemas.events import EventCreate, EventListResponse, EventDetail
from src.services import rule_service


def record(db: Session, payload: EventCreate) -> AuditEvent | None:
    """Применяет правила и сохраняет событие. Возвращает None если событие подавлено."""
    modified = rule_service.apply_rules(db, payload)
    if modified is None:
        return None
    return event_repo.insert(db, modified)


def record_admin_action(db: Session, payload: EventCreate) -> AuditEvent:
    """Сохраняет событие администратора loging_service, минуя правила (нельзя подавить).

    severity назначается из _DEFAULT_SEVERITY если не задан явно.
    """
    from src.services.rule_service import _resolve_default_severity
    if payload.severity is None:
        payload = payload.model_copy(
            update={"severity": _resolve_default_severity(payload.action, payload.status)}
        )
    return event_repo.insert(db, payload)


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
) -> EventListResponse:
    events, total = event_repo.query(
        db,
        department_id=department_id,
        service=service,
        severity=severity,
        action=action,
        from_time=from_time,
        to_time=to_time,
        limit=limit,
        offset=offset,
    )
    return EventListResponse(
        items=[EventDetail.model_validate(e) for e in events],
        total=total,
        limit=limit,
        offset=offset,
    )
