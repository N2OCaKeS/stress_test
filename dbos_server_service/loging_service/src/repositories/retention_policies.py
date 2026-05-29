"""Репозиторий `RetentionPolicy` — global или per-(severity, service) политики хранения.

PUT без `severity_filter`/`service_filter` живёт в legacy-режиме: один row
с NULL/NULL применяется ко всем событиям. PUT с filter'ами создаёт
Cartesian product (одна строка на пару) — `apply_active` обрабатывает их
как отдельные DELETE-проходы по соответствующим фильтрам.
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, delete, or_, select, update as sa_update
from sqlalchemy.orm import Session

from src.models.audit_event import AuditEvent
from src.models.retention_policy import RetentionPolicy
from src.schemas.retention import RetentionPolicyCreate

# Lowercase каноническая форма — `EventCreate.service` валидатор уже
# приводит к канонике (NFKC + invisibles + confusables + lower) на ingest,
# поэтому в БД `service` лежит готовый ASCII-snake_case. Сравнение здесь
# raw-equality по этой нормализованной колонке, без `func.lower(...)` —
# иначе теряется `ix_audit_events_service` index seek и retention-sweep
# вырождается в seq-scan по миллионам строк. Литерал `"loging_service"`
# защищён от ротации (см. `apply_active`).
_PROTECTED_SERVICE = "loging_service"


def get_active(db: Session) -> RetentionPolicy | None:
    """Возвращает единственную активную retention-политику (самую свежую)."""
    return db.execute(
        select(RetentionPolicy)
        .where(RetentionPolicy.is_active == True)  # noqa: E712
        .order_by(RetentionPolicy.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def create(db: Session, payload: RetentionPolicyCreate) -> RetentionPolicy:
    """Создаёт одну retention-политику (без severity/service-фильтров).

    Wrap'ер вокруг `create_policy` для backward-compat (один row, NULL/NULL).
    Возвращает «представительский» row, как делал старый код, — первый
    созданный.
    """
    rows = create_policy(db, payload)
    return rows[0]


def create_policy(
    db: Session, payload: RetentionPolicyCreate, *, commit: bool = True
) -> list[RetentionPolicy]:
    """Создаёт N×M retention-политик из Cartesian product (severity × service).

    Если оба filter'а пусты — пишет один row с NULL/NULL (legacy-поведение,
    применяется ко всем событиям). Иначе генерирует одну строку на каждую
    пару (severity, service), где None обрабатывается как «все severity» или
    «все сервисы» соответственно.

    Возвращает список созданных строк в порядке вставки.

    `commit=False` — для атомарного admin-CRUD: caller (endpoint)
    делает единственный `db.commit()` после deactivate + create + audit.
    """
    now = datetime.now(timezone.utc)
    severities = payload.severity_filter or [None]
    services = payload.service_filter or [None]
    created: list[RetentionPolicy] = []
    for sev in severities:
        for svc in services:
            policy = RetentionPolicy(
                severity=sev,
                service=svc,
                retain_days=payload.retain_days,
                description=payload.description,
                is_active=payload.is_active,
                created_at=now,
                updated_at=now,
            )
            db.add(policy)
            created.append(policy)
    if commit:
        db.commit()
        for p in created:
            db.refresh(p)
    else:
        db.flush()
    return created


def deactivate_all_active(db: Session, *, commit: bool = True) -> int:
    """Помечает is_active=False у всех текущих активных политик.

    Используется при PUT с filter'ами: новая filtered-политика заменяет
    предыдущий набор (single global или предыдущий Cartesian). Возвращает
    количество затронутых строк.

    `commit=False` — атомарный admin-CRUD: caller делает один commit
    после deactivate + create + audit.
    """
    now = datetime.now(timezone.utc)
    result = db.execute(
        sa_update(RetentionPolicy)
        .where(RetentionPolicy.is_active == True)  # noqa: E712
        .values(is_active=False, updated_at=now)
    )
    if commit:
        db.commit()
    else:
        db.flush()
    return result.rowcount


def list_active(db: Session) -> list[RetentionPolicy]:
    """Все активные политики, отсортированные от свежих к старым.

    Filter-режим создаёт несколько строк за один PUT — `get_active`
    возвращает только первую (legacy contract). Здесь — полный набор для
    применения в `apply_active`.
    """
    return list(
        db.execute(
            select(RetentionPolicy)
            .where(RetentionPolicy.is_active == True)  # noqa: E712
            .order_by(RetentionPolicy.created_at.desc())
        ).scalars().all()
    )


# Размер чанка для retention-DELETE. Один большой DELETE на миллионы строк
# держал бы row-locks на всю выборку, раздувал WAL и тормозил конкурентный
# ingest-INSERT. Чанкуем по этому размеру и коммитим каждый чанк — autovacuum
# успевает чистить dead tuples между коммитами, а транзакция остаётся короткой.
_SWEEP_CHUNK_SIZE = 10_000


def apply_active(db: Session, *, chunk_size: int = _SWEEP_CHUNK_SIZE) -> int:
    """Удаляет события старше `retain_days`, КРОМЕ событий `loging_service`.

    Активные политики комбинируются в одно OR-выражение: событие подлежит
    удалению, если попадает под предикат **хотя бы одной** политики. Это
    даёт честный счёт уникально удалённых строк — N-pass подход суммировал
    rowcount каждой политики и недосчитывал, когда события подпадали под
    несколько (вторая политика видела rowcount=0 на уже снесённых первой).

    Предикат одной политики:
        timestamp < now - retain_days
        AND (severity IS NULL OR event.severity = severity)
        AND (service  IS NULL OR event.service = service)

    NULL в `severity`/`service` колонке = «все severity / все сервисы»
    (legacy global-политика). Сравнение `service` идёт raw-equality
    по уже нормализованному значению (ingest-валидатор канонизирует),
    чтобы не терять `ix_audit_events_service` index seek.

    DELETE идёт чанками по `chunk_size` строк с коммитом на каждый чанк —
    на append-only журнале в миллионы строк один безлимитный DELETE держал бы
    блокировки и раздувал WAL, тормозя ingest.

    Кросс-репликовая защита (один sweep за раз) обеспечивается session-level
    `pg_try_advisory_lock` в `main._retention_loop` — он переживает
    per-chunk коммиты, потому что advisory-lock привязан к сессии, а не к
    транзакции. Здесь дополнительного лока нет, чтобы прямой вызов
    `apply_active` (тесты, ручной прогон) не конфликтовал с daemon'ом.

    Возвращает количество уникально удалённых событий.
    """
    policies = list_active(db)
    if not policies:
        return 0

    now = datetime.now(timezone.utc)
    policy_predicates = []
    for policy in policies:
        cutoff = now - timedelta(days=policy.retain_days)
        clauses = [AuditEvent.timestamp < cutoff]
        if policy.severity is not None:
            clauses.append(AuditEvent.severity == policy.severity)
        if policy.service is not None:
            clauses.append(AuditEvent.service == policy.service)
        policy_predicates.append(and_(*clauses))

    # Один OR на все политики — событие удаляется, если попадает под любую.
    match_any = or_(*policy_predicates)
    id_select = (
        select(AuditEvent.id)
        .where(
            match_any,
            # `service` уже нормализован на ingest (NFKC + invisibles +
            # confusables + lower), поэтому raw-equality достаточно и
            # даёт index seek по `ix_audit_events_service`.
            AuditEvent.service != _PROTECTED_SERVICE,
        )
        .limit(chunk_size)
    )

    total = 0
    while True:
        stmt = delete(AuditEvent).where(
            AuditEvent.id.in_(id_select.scalar_subquery())
        )
        result = db.execute(stmt)
        db.commit()
        deleted = result.rowcount
        total += deleted
        if deleted < chunk_size:
            break
    return total
