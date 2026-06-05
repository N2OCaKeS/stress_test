"""Бизнес-логика приёма и чтения событий аудита."""

import logging
import traceback
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from src.core.exceptions import AppException, ConflictError
from src.models.audit_event import AuditEvent
from src.repositories import events as event_repo
from src.schemas.events import EventCreate, EventListResponse, EventDetail
from src.services import rule_service
from src.utils.redaction import redact

logger = logging.getLogger(__name__)


def _redact_payload(payload: EventCreate) -> EventCreate:
    """Применяет defense-in-depth маскировку к payload.details.

    Если details пуст или после маскировки не изменился — возвращает исходный
    объект, чтобы избежать ненужного `model_copy` (мелкая аллокация на каждом
    POST /events).

    Сложность сравнения `cleaned == payload.details`: для dict Python
    сравнивает рекурсивно, обходя вложенные dict/list. На допустимом payload'е
    глубина ≤10 (`EventCreate._details_depth`) и общий объём ≤64 KB
    (`_details_size`), так что сравнение O(N) по числу leaf-значений и
    укладывается в десятки микросекунд даже на пограничных payload'ах.
    Атаку «сделай compare экспоненциально дорогим» не запустить — структурный
    DoS отсечён валидаторами схемы выше по стеку.
    """
    if not payload.details:
        return payload
    cleaned = redact(payload.details)
    if cleaned == payload.details:
        return payload
    return payload.model_copy(update={"details": cleaned})


def record(db: Session, payload: EventCreate) -> AuditEvent | None:
    """Применяет правила и сохраняет событие. Возвращает None если событие подавлено.

    На `IDEMPOTENCY_KEY_CONFLICT` (хэш payload'а разошёлся с тем, что лежит
    под этим (service, idempotency_key)) эмитим WARNING self-audit и
    пробрасываем 409 caller'у. Self-audit писать ПОСЛЕ rollback'а основной
    транзакции репозитория (insert завалил savepoint при raise) — иначе
    sqlalchemy ругается на dirty session.

    Cold-start contract: если БД упала и rule-cache ещё не наполнялся,
    `apply_rules` пробросит исключение → caller получит 500 → outbox retry.
    Это fail-closed: для audit-журнала важнее «событие либо отработано
    правилами, либо упало и поедет на retry», чем «отработать без правил».
    Записать ingest без rule engine означало бы тайно пропустить события,
    которые SUPPRESS-правило должно было дропнуть — compliance-дыра.
    """
    payload = _redact_payload(payload)
    modified = rule_service.apply_rules(db, payload)
    if modified is None:
        return None
    try:
        return event_repo.insert(db, modified)
    except ConflictError as exc:
        # Откат завалившейся вставки, чтобы self-audit писался в чистой сессии.
        # Если rollback сам падает (disconnect, broken pool), session
        # гарантированно непригодна для последующего INSERT'а — self-audit
        # на ней почти наверняка либо завалится молча, либо отравит outer
        # error caller'у. Логируем CRITICAL (rollback-fail после
        # ConflictError — это или баг в SQLAlchemy-сессии, или серьёзный
        # сбой пула) и пропускаем self-audit; outer ConflictError всё
        # равно поднимется, клиент получит 409.
        rollback_ok = True
        try:
            db.rollback()
        except Exception as rb_exc:
            rollback_ok = False
            logger.critical(
                "rollback after idempotency conflict failed: %s; "
                "skipping self-audit emit on broken session",
                rb_exc,
            )
        if rollback_ok:
            _emit_idempotency_conflict_audit(db, modified, exc)
        raise


def _emit_idempotency_conflict_audit(
    db: Session, payload: EventCreate, exc: ConflictError
) -> None:
    """Самоаудит факта обнаружения poisoning'а на (service, idempotency_key).

    Пишется отдельной транзакцией от основного insert'а (тот уже завалился).
    Сценарий редкий, поэтому накладные расходы на second commit допустимы.
    Любая ошибка тут проглатывается в лог — клиент всё равно должен получить
    409, а потеря warning self-audit'а не должна мешать основной ошибке.

    ИНВАРИАНТ caller'а: вызывается только ПОСЛЕ rollback'а outer-tx
    (см. `record` выше). `commit=True` ниже закрывает свою отдельную
    транзакцию, и если outer-tx не была rollback'нута, этот commit
    выкинет её partially-committed состояние наружу. Сейчас единственный
    call-site — ConflictError-ветка `record`, и rollback там стоит явно.
    Если в будущем появится другой call-site (например, валидатор, который
    зовёт `_emit_idempotency_conflict_audit` без явного rollback'а
    собственной savepoint'ы), commit-семантика становится сюрпризом —
    стоит вынести `commit_mode` параметром или продублировать инвариант
    в docstring caller'а.
    """
    try:
        warning_payload = EventCreate(
            timestamp=datetime.now(timezone.utc),
            service="loging_service",
            action="audit.idempotency_conflict",
            actor_id=None,
            actor_type="service",
            username=None,
            status="warning",
            allowed=True,
            severity="WARNING",
            details={
                "claimed_service": payload.service,
                "idempotency_key": payload.idempotency_key,
                "claimed_action": payload.action,
                "error_code": exc.error_code,
            },
        )
        record_admin_action(db, warning_payload, commit=True)
    except Exception as audit_exc:
        logger.warning(
            "self-audit for IDEMPOTENCY_KEY_CONFLICT failed: %s", audit_exc
        )


def record_admin_action(
    db: Session, payload: EventCreate, *, commit: bool = True
) -> AuditEvent:
    """Сохраняет событие администратора loging_service, минуя правила (нельзя подавить).

    severity назначается из _DEFAULT_SEVERITY если не задан явно.

    Технический guard от рефакторинга: функция обходит `apply_rules`, поэтому
    любой `payload.service != "loging_service"` превратил бы её в универсальный
    bypass правил для чужих сервисов. Используем `AppException(500)` (а не
    `assert`), чтобы invariant сохранялся даже под `python -O`, и чтобы FastAPI
    отдал стандартизованный 500-ответ через `app_exception_handler`.

    `commit=False` — для admin-CRUD: основная DML-операция и audit идут в
    одной транзакции, итоговый `db.commit()` делает endpoint.
    """
    from src.services.rule_service import _resolve_default_severity
    if payload.service != "loging_service":
        # Логируем стек вызова, чтобы invariant-500 не маскировал bug call-site:
        # traceback покажет, какой endpoint вызвал record_admin_action с чужим сервисом.
        caller = traceback.extract_stack(limit=4)[-2]
        logger.error(
            "record_admin_action invariant violation: service=%r at %s:%d in %s",
            payload.service, caller.filename, caller.lineno, caller.name,
        )
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
    return event_repo.insert(db, payload, commit=commit)


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
    include_total: bool = False,
    identity: dict | None = None,
) -> EventListResponse:
    timeout_state: dict = {}
    events, total, has_more = event_repo.query(
        db,
        department_id=department_id,
        service=service,
        severity=severity,
        action=action,
        from_time=from_time,
        to_time=to_time,
        limit=limit,
        offset=offset,
        include_total=include_total,
        timeout_state=timeout_state,
    )
    if timeout_state:
        _emit_query_timeout_audit(
            db,
            identity=identity,
            timeout_state=timeout_state,
            filters={
                "department_id": department_id,
                "service": service,
                "severity": severity,
                "action": action,
                "from_time": from_time.isoformat() if from_time else None,
                "to_time": to_time.isoformat() if to_time else None,
                "limit": limit,
                "offset": offset,
                "include_total": include_total,
            },
        )
    return EventListResponse(
        items=[EventDetail.model_validate(e) for e in events],
        total=total,
        has_more=has_more,
        limit=limit,
        offset=offset,
    )


def _emit_query_timeout_audit(
    db: Session,
    *,
    identity: dict | None,
    timeout_state: dict,
    filters: dict,
) -> None:
    """WARNING self-audit на отмену COUNT/SELECT по statement_timeout.

    Без этого события «100 timeout-ов в минуту с одного reader-JWT» (флуд
    широкими COUNT'ами или умышленный DoS на pgsql-pool) остаётся виден
    только в server-логах. Эмитим `logging.events_queried` со статусом
    `warning` и пробросом флагов timeout'а в details, чтобы SIEM-rule
    мог поднять алёрт по action+status+actor.

    Отдельная транзакция (`commit=True`): caller `query()` ничего ещё не
    коммитил, а сам SELECT уже отработал через savepoint — состояние
    сессии чистое. Любая ошибка self-audit'а проглатывается в лог, чтобы
    не маскировать legitimate response caller'у.
    """
    try:
        actor_type = (identity or {}).get("actor_type") or "user"
        details = {
            "timeout": True,
            "count_timeout": bool(timeout_state.get("count_timeout")),
            "query_timeout": bool(timeout_state.get("query_timeout")),
            "filters": filters,
        }
        warning_payload = EventCreate(
            timestamp=datetime.now(timezone.utc),
            service="loging_service",
            action="logging.events_queried",
            actor_id=(identity or {}).get("user_id"),
            actor_type=actor_type,
            username=(identity or {}).get("username"),
            department_id=(identity or {}).get("department_id"),
            status="warning",
            allowed=True,
            severity="WARNING",
            details=details,
        )
        record_admin_action(db, warning_payload, commit=True)
    except Exception as audit_exc:
        logger.warning(
            "self-audit for events_queried timeout failed: %s", audit_exc
        )
