"""Бизнес-логика приёма и чтения событий аудита."""

from datetime import datetime

from sqlalchemy.orm import Session

from src.core.exceptions import AppException
from src.models.audit_event import AuditEvent
from src.repositories import events as event_repo
from src.schemas.events import EventCreate, EventListResponse, EventDetail
from src.services import rule_service
from src.utils.redaction import redact


def _redact_payload(payload: EventCreate) -> EventCreate:
    """Применяет defense-in-depth маскировку к payload.details.
    Если details пуст или после маскировки не изменился — возвращает исходный объект.
    """
    if not payload.details:
        return payload
    cleaned = redact(payload.details)
    if cleaned == payload.details:
        return payload
    return payload.model_copy(update={"details": cleaned})


def record(db: Session, payload: EventCreate) -> AuditEvent | None:
    """Применяет правила и сохраняет событие. Возвращает None если событие подавлено."""
    payload = _redact_payload(payload)
    modified = rule_service.apply_rules(db, payload)
    if modified is None:
        return None
    return event_repo.insert(db, modified)


def record_admin_action(db: Session, payload: EventCreate) -> AuditEvent:
    """Сохраняет событие администратора loging_service, минуя правила (нельзя подавить).

    severity назначается из _DEFAULT_SEVERITY если не задан явно.

    Технический guard от рефакторинга: функция обходит `apply_rules`, поэтому
    любой `payload.service != "loging_service"` превратил бы её в универсальный
    bypass правил для чужих сервисов. Используем `AppException(500)` (а не
    `assert`), чтобы invariant сохранялся даже под `python -O`, и чтобы FastAPI
    отдал стандартизованный 500-ответ через `app_exception_handler`.
    """
    from src.services.rule_service import _resolve_default_severity
    if payload.service != "loging_service":
        raise AppException(
            error_code="ADMIN_AUDIT_WRONG_SERVICE",
            message=(
                "record_admin_action only for self-audit "
                f"(service must be 'loging_service', got {payload.service!r})"
            ),
            http_status=500,
        )
    payload = _redact_payload(payload)
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
