"""Репозиторий `AuditEvent` — только insert и query, никогда update/delete."""

from datetime import datetime, timezone

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from src.models.audit_event import AuditEvent
from src.schemas.events import EventCreate
from src.utils.ids import audit_event_id


def insert(db: Session, payload: EventCreate, *, commit: bool = True) -> AuditEvent:
    """Вставляет новое событие аудита с idempotency по `(service, idempotency_key)`.

    Когда `payload.idempotency_key` задан, два POST'а с одинаковой
    `(service, idempotency_key)` дедуплицируются через PostgreSQL'овский
    `ON CONFLICT … DO NOTHING` против partial UNIQUE индекса
    `uq_audit_events_service_idempotency_key` (см. миграцию `h8c9d0e1f2a3`).
    Второй вызов возвращает **уже-сохранённый** row, не новый — caller'ы
    эндпоинта на retry получают тот же `id` / `received_at`, поэтому HTTP
    retry outbox-publisher'а безопасен.

    Когда `payload.idempotency_key` — None, дедуп не происходит. Legacy
    caller'ы / one-shot ingest продолжают вставлять безусловно.

    `commit=False` — для атомарного admin-CRUD: основной DML и audit
    шарят одну транзакцию, единственный `db.commit()` делает caller.
    """
    new_id = audit_event_id()
    received = datetime.now(timezone.utc)

    if payload.idempotency_key is None:
        # Fast path — legacy ingest без idempotency. Plain ORM add.
        event = AuditEvent(
            id=new_id,
            timestamp=payload.timestamp,
            received_at=received,
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
            idempotency_key=None,
        )
        db.add(event)
        if commit:
            db.commit()
            db.refresh(event)
        else:
            db.flush()
        return event

    # Idempotent path — INSERT … ON CONFLICT (service, idempotency_key) DO
    # NOTHING. Если row с таким же ключом для этого сервиса уже есть,
    # `RETURNING id` пустой, и мы достаём канонический row по natural key —
    # caller увидит **first-write** id/received_at (outbox-retry safe).
    # `severity` на этой стадии может быть NULL, если default-severity
    # resolver не задел этот row — у ORM-колонки `default="INFO"`, но
    # `pg_insert.values(...)` python-side defaults скипает, так что
    # подставляем явно, чтобы держать NOT NULL invariant.
    values = {
        "id": new_id,
        "timestamp": payload.timestamp,
        "received_at": received,
        "service": payload.service,
        "action": payload.action,
        "actor_id": payload.actor_id,
        "actor_type": payload.actor_type,
        "username": payload.username,
        "department_id": payload.department_id,
        "target_id": payload.target_id,
        "target_type": payload.target_type,
        "status": payload.status,
        "allowed": payload.allowed,
        "severity": payload.severity or "INFO",
        "request_id": payload.request_id,
        "details": payload.details,
        "idempotency_key": payload.idempotency_key,
    }
    # ON CONFLICT против PARTIAL UNIQUE индекса требует повторить index-предикат
    # (`WHERE idempotency_key IS NOT NULL`) в `index_where` — иначе PostgreSQL
    # ругается «there is no unique or exclusion constraint matching the ON
    # CONFLICT specification» (full-index ON CONFLICT не матчит partial).
    stmt = (
        pg_insert(AuditEvent)
        .values(**values)
        .on_conflict_do_nothing(
            index_elements=["service", "idempotency_key"],
            index_where=text("idempotency_key IS NOT NULL"),
        )
        .returning(AuditEvent.id)
    )
    result = db.execute(stmt).scalar_one_or_none()
    if commit:
        db.commit()
    else:
        db.flush()

    if result is not None:
        # Свежая вставка — достаём row, который только что записали, чтобы
        # caller получил populated ORM-инстанс (как в non-idempotent ветке).
        return db.execute(
            select(AuditEvent).where(AuditEvent.id == result)
        ).scalar_one()

    # Conflict — возвращаем канонический row (тот, с которым мы пытались
    # дедупиться). Это row, который первый POST caller'а сохранил.
    return db.execute(
        select(AuditEvent).where(
            AuditEvent.service == payload.service,
            AuditEvent.idempotency_key == payload.idempotency_key,
        )
    ).scalar_one()


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
) -> tuple[list[AuditEvent], int | None, bool]:
    """Постранично достаёт события + признак `has_more` без обязательного COUNT.

    На append-only журнале в миллионы строк COUNT(*) по широкому фильтру —
    второй полный проход на каждый read. Поэтому total считается только когда
    `include_total=True` (явный запрос точного числа для дашборда/пагинатора).
    В остальных случаях возвращаем `None` и определяем «есть ли ещё страница»
    через выборку `limit + 1` строк — один scan вместо двух.

    Возвращает `(events, total, has_more)`, где `total` равен None при
    `include_total=False`.
    """
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

    total = db.execute(count_stmt).scalar_one() if include_total else None

    # Берём на одну строку больше запрошенного лимита — лишняя строка говорит,
    # что за текущей страницей есть ещё данные. Её саму в выдачу не отдаём.
    rows = db.execute(
        stmt.order_by(AuditEvent.timestamp.desc()).offset(offset).limit(limit + 1)
    ).scalars().all()

    has_more = len(rows) > limit
    events = list(rows[:limit])

    return events, total, has_more


def list_services(db: Session, *, department_id: str | None = None) -> list:
    """Агрегат `event_count` / `last_event_at` по каждому сервису.

    Когда `department_id` задан — результаты ограничены событиями отдела:
    и count, и `last_event_at` отражают только rows, где
    `AuditEvent.department_id == department_id`. Сервисы, ничего не писавшие
    из этого отдела, выпадают (`HAVING COUNT(*) > 0` падает естественно из
    GROUP BY + WHERE).

    Это scope-leak фикс для `GET /services`: dept-scoped reader не должен
    видеть cross-department `event_count`/`last_event_at`. Endpoint
    (`endpoints/services.py::list_services`) решает, передавать ли scope —
    зеркалит dept-scope в `GET /events`.

    `department_id is None` — legacy глобальный агрегат, используется
    unscoped reader'ами (`loging_admin` / `account_admin`).
    """
    stmt = (
        select(
            AuditEvent.service,
            func.count(AuditEvent.id).label("event_count"),
            func.max(AuditEvent.timestamp).label("last_event_at"),
        )
        .group_by(AuditEvent.service)
        .order_by(AuditEvent.service)
    )
    if department_id is not None:
        stmt = stmt.where(AuditEvent.department_id == department_id)
    return db.execute(stmt).all()
