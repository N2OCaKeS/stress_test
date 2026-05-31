"""Репозиторий `AuditEvent` — только insert и query, никогда update/delete."""

import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from typing import Callable, TypeVar

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from src.core.config import get_settings
from src.core.exceptions import ConflictError, DomainValidationError
from src.models.audit_event import AuditEvent
from src.schemas.events import EventCreate
from src.utils.ids import audit_event_id

logger = logging.getLogger(__name__)

_T = TypeVar("_T")


def _with_statement_timeout(
    db: Session,
    timeout_ms: int,
    fn: Callable[[], _T],
    *,
    on_canceled: Callable[[], _T],
    canceled_log_msg: str,
) -> _T:
    """Прогоняет `fn()` под `SET LOCAL statement_timeout = <ms>` в SAVEPOINT'е.

    Postgres не принимает bind-параметры в `SET LOCAL`, поэтому `timeout_ms`
    инлайнится — значение валидируется в Settings (int + ge=0), SQL-инъекция
    исключена. SAVEPOINT нужен, чтобы query_canceled (57014) не повалил
    активную внешнюю транзакцию.

    На `57014` зовётся `on_canceled()` и пишется warning. Любая другая
    DBAPIError пробрасывается caller'у.

    Quirk: `SET LOCAL` действует до конца внешней транзакции, а не до
    выхода из SAVEPOINT. После rollback'а внутреннего nested-savepoint'а
    выставленный timeout продолжит действовать на следующие statement'ы
    в той же outer-tx, пока она не закоммитится/отроллбэчится. Под
    текущих caller'ов (per-request read-эндпоинт с фиксированным session
    scope'ом) это безопасно — outer-tx закрывается сразу после ответа.
    Если helper позовут из долгоживущего сценария (worker-таска,
    batch-loop с одной транзакцией), выставленный timeout будет
    наследоваться следующими операциями этой же tx.

    На `timeout_ms=0` SET LOCAL пропускаем — Postgres трактует 0 как
    «без лимита», но сам факт записи в session-state остаётся, и в
    долгоживущих транзакциях это сбрасывало бы ранее выставленный
    timeout. No-op ветка явная.
    """
    nested = db.begin_nested()
    try:
        if timeout_ms > 0:
            timeout_sql = f"SET LOCAL statement_timeout = {int(timeout_ms)}"
            db.execute(text(timeout_sql))
        result = fn()
        nested.commit()
        return result
    except DBAPIError as exc:
        nested.rollback()
        pgcode = getattr(getattr(exc.orig, "pgcode", None), "value", None) \
            or getattr(exc.orig, "pgcode", None)
        if pgcode == "57014":
            logger.warning(canceled_log_msg, timeout_ms)
            return on_canceled()
        raise

# Defence-in-depth: схема `EventCreate` уже валидирует request_id, но
# репозиторий могут дёрнуть напрямую из миграции, фоновой задачи или
# другого сервиса минуя Pydantic. CR/LF в request_id попадает в
# `X-Request-ID` рефлектом middleware и колется header-injection,
# поэтому страхуемся ещё одним фильтром перед самим INSERT.
# Charset зеркалит `schemas.events._REQUEST_ID_PATTERN` — точка разрешена
# (`req.<id>` / `trace.<span>` convention), без асимметрии не пропускали
# бы request-id'шки, которые схема уже легально приняла.
_REQUEST_ID_RE: re.Pattern[str] = re.compile(r"^[A-Za-z0-9_.\-]{1,64}$")


def _validate_request_id(value: str | None) -> None:
    if value is None:
        return
    if not _REQUEST_ID_RE.match(value):
        raise DomainValidationError(
            error_code="INVALID_REQUEST_ID",
            message="request_id must match ^[A-Za-z0-9_.-]{1,64}$",
        )


# Поля, входящие в hash payload'а для idempotency-poisoning защиты.
# Сюда НЕ входят `received_at` и `id` (server-side defaults — на двух
# ретраях будут разные значения). `request_id` тоже исключён: middleware
# на ретраях может реассайнить его, а сама retry-семантика не должна
# зависеть от значения трассировки. Все остальные поля EventCreate
# участвуют в хэше: разные значения → разный logical event → 409.
_HASH_FIELDS: tuple[str, ...] = (
    "timestamp",
    "service",
    "action",
    "actor_id",
    "actor_type",
    "username",
    "department_id",
    "target_id",
    "target_type",
    "status",
    "allowed",
    "severity",
    "details",
    "idempotency_key",
)


def _payload_hash(payload: EventCreate) -> str:
    """SHA-256 от canonical-JSON repr того, что определяет логику события.

    Canonical-JSON = `json.dumps(..., sort_keys=True, separators=(',', ':'),
    default=str)`. Сортировка ключей и default=str гарантируют, что одинаковый
    payload даст одинаковый хэш на любой Python-реализации, не зависящий от
    insertion-order'а dict'ов и наличия datetime'ов в `details` / `timestamp`.

    Используется на CONFLICT-ветке `insert()` для отличения легитимного
    outbox-retry'я (one и тот же hash → 200, существующий row) от
    idempotency-poisoning'а (другой hash → 409 IDEMPOTENCY_KEY_CONFLICT).
    """
    dumped = payload.model_dump(mode="json")
    canonical = {k: dumped.get(k) for k in _HASH_FIELDS}
    blob = json.dumps(canonical, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


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
    _validate_request_id(payload.request_id)

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
    # NOTHING + payload-hash compare. Если row с таким же ключом для этого
    # сервиса уже есть, `RETURNING id` пустой; мы достаём существующий row
    # и сверяем его `idempotency_payload_hash` с хэшем текущего запроса.
    # Совпало → idempotent replay, возвращаем существующий (caller получит
    # тот же id/received_at). Не совпало → 409 IDEMPOTENCY_KEY_CONFLICT —
    # защита от idempotency-poisoning: атакующий с `SERVICE_API_KEY` мог бы
    # заранее «застолбить» ключ X фейковым payload'ом, и легитимный сервис
    # на ретрае молча получил бы чужое (а свой реальный audit-row терял).
    #
    # `severity` на этой стадии может быть NULL, если default-severity
    # resolver не задел этот row — у ORM-колонки `default="INFO"`, но
    # `pg_insert.values(...)` python-side defaults скипает, так что
    # подставляем явно, чтобы держать NOT NULL invariant.
    payload_hash = _payload_hash(payload)
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
        "idempotency_payload_hash": payload_hash,
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

    # Conflict — достаём канонический row и сверяем hash payload'а.
    existing = db.execute(
        select(AuditEvent).where(
            AuditEvent.service == payload.service,
            AuditEvent.idempotency_key == payload.idempotency_key,
        )
    ).scalar_one()

    # Legacy row, записанный до миграции j0e1f2a3b4c5, мог не иметь hash'а.
    # Это редкий backward-compat случай: возвращаем существующий row без
    # 409, чтобы не ломать ingest на старых данных. Новые row'ы всегда
    # получают hash при вставке выше.
    if existing.idempotency_payload_hash is None:
        return existing

    if existing.idempotency_payload_hash != payload_hash:
        # Idempotency poisoning detected — payload разошёлся с тем, что
        # лежит под этим (service, idempotency_key). Caller увидит 409;
        # warning-self-audit эмитит event_service слоем выше (репозиторий
        # к self-audit не цепляем — это разрушит атомарность вызова).
        raise ConflictError(
            error_code="IDEMPOTENCY_KEY_CONFLICT",
            message=(
                "idempotency_key already used by a different payload "
                "for this service"
            ),
            details={
                "service": payload.service,
                "idempotency_key": payload.idempotency_key,
            },
        )
    return existing


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
    settings = get_settings()
    stmt = select(AuditEvent)

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

    if include_total:
        count_stmt = select(func.count()).select_from(AuditEvent)
        for f in filters:
            count_stmt = count_stmt.where(f)
        # COUNT по миллионам строк — отдельный seq-scan, который легко
        # держит pooled-коннект 10+ секунд. Ограничиваем длительность
        # сессионным `statement_timeout`; на превышении Postgres шлёт
        # `57014 query_canceled`, мы возвращаем `total=None` (caller
        # документирован как "None = точное число неизвестно") и не
        # ломаем основной выпуск страницы.
        timeout_ms = settings.audit_count_statement_timeout_ms
        if timeout_ms > 0:
            total = _with_statement_timeout(
                db,
                timeout_ms,
                lambda: db.execute(count_stmt).scalar_one(),
                on_canceled=lambda: None,
                canceled_log_msg=(
                    "audit COUNT exceeded statement_timeout=%dms; returning total=None"
                ),
            )
        else:
            total = db.execute(count_stmt).scalar_one()
    else:
        total = None

    # Берём на одну строку больше запрошенного лимита — лишняя строка говорит,
    # что за текущей страницей есть ещё данные. Её саму в выдачу не отдаём.
    page_stmt = stmt.order_by(AuditEvent.timestamp.desc()).offset(offset).limit(limit + 1)

    # Основной SELECT тоже под guard'ом: широкий фильтр + большой OFFSET
    # умеет уйти в долгий seq-scan и забить пул коннектов так же, как
    # COUNT'у. Семантика на превышение — пустая страница + warning лог
    # (а не 500): caller продолжает работать с пустым результатом,
    # дашборд не падает целиком.
    query_timeout_ms = settings.audit_query_statement_timeout_ms
    if query_timeout_ms > 0:
        rows = _with_statement_timeout(
            db,
            query_timeout_ms,
            lambda: db.execute(page_stmt).scalars().all(),
            on_canceled=lambda: [],
            canceled_log_msg=(
                "audit SELECT exceeded statement_timeout=%dms; returning empty page"
            ),
        )
    else:
        rows = db.execute(page_stmt).scalars().all()

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
