"""Бизнес-логика приёма и чтения событий аудита."""

import logging
import os
import traceback
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from src.core.exceptions import AppException, ConflictError
from src.models.audit_event import AuditEvent
from src.repositories import events as event_repo
from src.schemas.events import EventCreate, EventListResponse, EventDetail, EventStatsResponse
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
        # Если rollback сам падает (disconnect, broken pool), эта session
        # непригодна для последующего INSERT'а. Раньше тут self-audit просто
        # пропускался — poisoning-инцидент терялся для SOC при сбое пула,
        # хотя именно сбой пула + конфликт ключа = подозрительная связка.
        # Теперь на broken-rollback пишем self-audit СВЕЖЕЙ сессией из пула
        # (новый коннект), а не молчим. Outer ConflictError в любом случае
        # поднимается — клиент получит 409.
        rollback_ok = True
        try:
            db.rollback()
        except Exception as rb_exc:
            rollback_ok = False
            logger.critical(
                "rollback after idempotency conflict failed: %s; "
                "retrying self-audit emit on a fresh session",
                rb_exc,
            )
        if rollback_ok:
            _emit_idempotency_conflict_audit(db, modified, exc)
        else:
            _emit_idempotency_conflict_audit_fresh_session(modified, exc)
        raise


def _emit_idempotency_conflict_audit(
    db: Session, payload: EventCreate, exc: ConflictError
) -> None:
    """Самоаудит факта обнаружения poisoning'а на (service, idempotency_key).

    Пишется отдельной транзакцией от основного insert'а (тот уже завалился).
    Сценарий редкий, поэтому накладные расходы на second commit допустимы.
    Любая ошибка тут проглатывается в лог — клиент всё равно должен получить
    409, а потеря warning self-audit'а не должна мешать основной ошибке.

    Штатный call-site (`record` выше) зовёт нас ПОСЛЕ rollback'а — сессия
    вне транзакции, и мы пишем self-audit отдельной tx (`commit=True`).
    Но функцию могут позвать и из caller'а, который держит СВОЮ explicit
    `db.begin()` (admin-CRUD под одной транзакцией). Тогда голый
    `commit=True` снёс бы чужую транзакцию, а ранний `return` тихо терял
    бы диагностику конфликта. Различаем оба режима по `db.in_transaction()`:

    * вне транзакции — пишем self-audit отдельной tx (`commit=True`);
    * внутри живой outer-tx — пишем в SAVEPOINT (`begin_nested`) с
      `commit=False`, фиксируем только nested-транзакцию. Outer-tx остаётся
      нетронутой: caller сам решит commit/rollback. Так self-audit не
      теряется и при этом мы не клоббрим чужую транзакцию.
    """
    warning_payload = EventCreate(
        timestamp=datetime.now(timezone.utc),
        service="loging_service",
        action="audit.idempotency_conflict",
        actor_id=None,
        actor_type="service",
        username=None,
        status="warning",
        allowed=True,
        # severity не задаём явно — `record_admin_action` подтягивает её из
        # `_DEFAULT_SEVERITY[("audit.idempotency_conflict", "warning")]`.
        # Раньше тут стояло хардкод-значение "WARNING", дублировавшее таблицу:
        # подняли бы severity в таблице до CRITICAL — событие всё равно
        # уезжало бы WARNING'ом из-за explicit override, источник истины
        # расходился бы со SIEM-rule'ами.
        severity=None,
        details={
            "claimed_service": payload.service,
            "idempotency_key": payload.idempotency_key,
            "claimed_action": payload.action,
            "error_code": exc.error_code,
        },
    )
    if db.in_transaction():
        # Caller держит свою транзакцию — пишем в savepoint, чтобы не трогать
        # её commit/rollback boundary. `record_admin_action(commit=False)`
        # делает flush в рамках nested-tx; её и фиксируем.
        nested = db.begin_nested()
        try:
            record_admin_action(db, warning_payload, commit=False)
            nested.commit()
        except Exception as audit_exc:
            try:
                nested.rollback()
            except Exception:
                pass
            logger.warning(
                "self-audit for IDEMPOTENCY_KEY_CONFLICT failed (nested): %s",
                audit_exc,
            )
        return
    try:
        record_admin_action(db, warning_payload, commit=True)
    except Exception as audit_exc:
        logger.warning(
            "self-audit for IDEMPOTENCY_KEY_CONFLICT failed: %s", audit_exc
        )


def _emit_idempotency_conflict_audit_fresh_session(
    payload: EventCreate, exc: ConflictError
) -> None:
    """Аварийный self-audit конфликта на свежей сессии из пула.

    Зовётся, когда `db.rollback()` основной сессии сам упал (disconnect /
    broken pool) и писать что-либо на той сессии нельзя. Берём новый коннект
    из `SessionLocal` — у него своя транзакция и здоровое соединение, так что
    poisoning-инцидент всё-таки попадает в журнал, а не теряется молча.

    Любая ошибка здесь (БД легла целиком) проглатывается в лог — клиент в
    любом случае получает 409, а недоступность self-audit'а не должна
    маскировать основную ошибку.
    """
    from src.db.session import SessionLocal

    try:
        fresh = SessionLocal()
    except Exception as session_exc:
        logger.error(
            "could not open fresh session for IDEMPOTENCY_KEY_CONFLICT "
            "self-audit: %s",
            session_exc,
        )
        return
    try:
        _emit_idempotency_conflict_audit(fresh, payload, exc)
    finally:
        try:
            fresh.close()
        except Exception:
            pass


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
        # Пишем только basename файла и lineno + имя функции, без абсолютного пути:
        # log-aggregation внешний, не нужно сливать внутреннюю раскладку проекта.
        caller = traceback.extract_stack(limit=4)[-2]
        caller_basename = os.path.basename(caller.filename)
        logger.error(
            "record_admin_action invariant violation: service=%r at %s:%d in %s",
            payload.service, caller_basename, caller.lineno, caller.name,
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
        # Catalog lookup тоже доступен self-audit'у — если кто-то когда-нибудь
        # переименует logging.* action и зарегистрирует свой severity через
        # `register_events`, hardcoded таблица всё равно поймает (там self-audit
        # actions присутствуют), но передаём db для симметрии с `apply_rules`.
        payload = payload.model_copy(
            update={"severity": _resolve_default_severity(payload.action, payload.status, db)}
        )
    return event_repo.insert(db, payload, commit=commit)


def query(
    db: Session,
    *,
    department_id: str | None = None,
    service: str | None = None,
    severity: str | None = None,
    action: str | None = None,
    actor_id: str | None = None,
    actor_ip: str | None = None,
    target_id: str | None = None,
    status: str | None = None,
    request_id: str | None = None,
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
        actor_id=actor_id,
        actor_ip=actor_ip,
        target_id=target_id,
        status=status,
        request_id=request_id,
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
            # `actor_id`/`actor_type` зарезервированы как top-level audit-колонки
            # на _любой_ глубине вложения в details (см. `_RESERVED_DETAIL_KEYS`
            # в `schemas/events.py`). Префиксуем фильтр-ключи, чтобы они не
            # коллидили с гардом — read-фильтр это не actor события, а критерий
            # отбора page'а в reader-side диагностике.
            filters={
                "department_id": department_id,
                "service": service,
                "severity": severity,
                "action": action,
                "f_actor_id": actor_id,
                "f_actor_ip": actor_ip,
                "f_target_id": target_id,
                "f_status": status,
                "f_request_id": request_id,
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


def stats(
    db: Session,
    *,
    department_id: str | None = None,
    service: str | None = None,
    severity: str | None = None,
    action: str | None = None,
    actor_id: str | None = None,
    actor_ip: str | None = None,
    target_id: str | None = None,
    status: str | None = None,
    request_id: str | None = None,
    from_time: datetime,
    to_time: datetime,
) -> EventStatsResponse:
    """Агрегаты за окно `[from_time, to_time)` — счётчики по severity/service/status."""
    agg = event_repo.aggregate_stats(
        db,
        department_id=department_id,
        service=service,
        severity=severity,
        action=action,
        actor_id=actor_id,
        actor_ip=actor_ip,
        target_id=target_id,
        status=status,
        request_id=request_id,
        from_time=from_time,
        to_time=to_time,
    )
    return EventStatsResponse(
        total=agg["total"],
        by_severity=agg["by_severity"],
        by_service=agg["by_service"],
        by_status=agg["by_status"],
        from_time=from_time,
        to_time=to_time,
        truncated=agg.get("truncated", False),
    )


def export_rows(
    db: Session,
    *,
    department_id: str | None = None,
    service: str | None = None,
    severity: str | None = None,
    action: str | None = None,
    actor_id: str | None = None,
    actor_ip: str | None = None,
    target_id: str | None = None,
    status: str | None = None,
    request_id: str | None = None,
    from_time: datetime,
    to_time: datetime,
    limit: int,
) -> tuple[list[AuditEvent], bool]:
    """Строки под фильтр для экспорта; `(rows, truncated)` (см. репозиторий)."""
    return event_repo.iter_export(
        db,
        department_id=department_id,
        service=service,
        severity=severity,
        action=action,
        actor_id=actor_id,
        actor_ip=actor_ip,
        target_id=target_id,
        status=status,
        request_id=request_id,
        from_time=from_time,
        to_time=to_time,
        limit=limit,
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
        # actor_type через тот же whitelist-резолв, что и outbox-drain:
        # неизвестное / отсутствующее значение → `anonymous` + WARNING, а не
        # тихий `"user"`. Голый `or "user"` помечал бы read-фильтр без
        # actor_type как пользовательскую активность (fake user для SOC).
        from src.services.audit_outbox import _resolve_actor_type
        actor_type = _resolve_actor_type((identity or {}).get("actor_type"))
        details = {
            "timeout": True,
            "count_timeout": bool(timeout_state.get("count_timeout")),
            "query_timeout": bool(timeout_state.get("query_timeout")),
            "filters": filters,
        }
        # severity не задаём явно — `record_admin_action` подтягивает её из
        # `_DEFAULT_SEVERITY[("logging.events_queried", "warning")]`.
        # Хардкод "WARNING" расходился бы с таблицей, если её когда-нибудь
        # поднимут до CRITICAL (массовый timeout-флуд = DoS-сигнал) — событие
        # всё равно уезжало бы WARNING'ом из-за explicit override. Зеркалит
        # фикс в `_emit_idempotency_conflict_audit`.
        warning_payload = EventCreate(
            timestamp=datetime.now(timezone.utc),
            service="loging_service",
            action="logging.events_queried",
            actor_id=(identity or {}).get("user_id"),
            actor_type=actor_type,
            username=(identity or {}).get("username"),
            department_id=(identity or {}).get("department_id"),
            department_name=(identity or {}).get("department_name"),
            status="warning",
            allowed=True,
            severity=None,
            details=details,
        )
        record_admin_action(db, warning_payload, commit=True)
    except Exception as audit_exc:
        logger.warning(
            "self-audit for events_queried timeout failed: %s", audit_exc
        )


def list_filter_options(db: Session, **kwargs) -> dict:
    return event_repo.list_filter_options(db, **kwargs)
