"""Репозиторий `RetentionPolicy` — global или per-(severity, service) политики хранения.

PUT без `severity_filter`/`service_filter` живёт в legacy-режиме: один row
с NULL/NULL применяется ко всем событиям. PUT с filter'ами создаёт
Cartesian product (одна строка на пару) — `apply_active` обрабатывает их
как отдельные DELETE-проходы по соответствующим фильтрам.
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select, update as sa_update
from sqlalchemy.orm import Session

from src.models.audit_event import AuditEvent
from src.models.retention_policy import RetentionPolicy
from src.schemas.retention import RetentionPolicyCreate, RetentionPolicyUpdate

# Lowercase каноническая форма — сравнение обязано быть case-insensitive,
# чтобы `service='LoGiNg_SeRvIcE'` не обходил retention-исключение.
#
# Защита от Unicode-bypass'ов (zero-width chars, кириллические/греческие
# confusables) форсится **на ingest** через
# `utils.normalization.normalize_service_name` в `EventCreate.service`
# pydantic-валидаторе: каждый свежезаписанный row уже в каноническом
# ASCII-form, и сравнения `func.lower(...) != 'loging_service'` ниже
# достаточно. Rows, записанные до того, как валидатор появился (legacy
# ingest), не могут содержать Unicode-confusables — ingest-схема это
# единственное pre-валидаторное место, где принимается внешний
# service-name (внутренние записи `main.py::_emit_audit` →
# `record_admin_action` хардкодят литерал `"loging_service"`).
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
    db: Session, payload: RetentionPolicyCreate
) -> list[RetentionPolicy]:
    """Создаёт N×M retention-политик из Cartesian product (severity × service).

    Если оба filter'а пусты — пишет один row с NULL/NULL (legacy-поведение,
    применяется ко всем событиям). Иначе генерирует одну строку на каждую
    пару (severity, service), где None обрабатывается как «все severity» или
    «все сервисы» соответственно.

    Возвращает список созданных строк в порядке вставки.
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
    db.commit()
    for p in created:
        db.refresh(p)
    return created


def deactivate_all_active(db: Session) -> int:
    """Помечает is_active=False у всех текущих активных политик.

    Используется при PUT с filter'ами: новая filtered-политика заменяет
    предыдущий набор (single global или предыдущий Cartesian). Возвращает
    количество затронутых строк.
    """
    now = datetime.now(timezone.utc)
    result = db.execute(
        sa_update(RetentionPolicy)
        .where(RetentionPolicy.is_active == True)  # noqa: E712
        .values(is_active=False, updated_at=now)
    )
    db.commit()
    return result.rowcount


def update(db: Session, policy: RetentionPolicy, payload: RetentionPolicyUpdate) -> RetentionPolicy:
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(policy, field, value)
    policy.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(policy)
    return policy


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

    Если активных политик с filter'ами несколько — каждая применяется
    отдельным набором DELETE по своей (severity, service)-комбинации. NULL в
    `severity`/`service` колонке = «все severity / все сервисы» (legacy
    global-политика).

    DELETE идёт чанками по `chunk_size` строк с коммитом на каждый чанк —
    на append-only журнале в миллионы строк один безлимитный DELETE держал бы
    блокировки и раздувал WAL, тормозя ingest. Цикл по политике крутится, пока
    очередной чанк удаляет полную пачку (есть что чистить дальше).

    Кросс-репликовая защита (один sweep за раз) обеспечивается session-level
    `pg_try_advisory_lock` в `main._retention_loop` — он переживает
    per-chunk коммиты, потому что advisory-lock привязан к сессии, а не к
    транзакции. Здесь дополнительного лока нет, чтобы прямой вызов
    `apply_active` (тесты, ручной прогон) не конфликтовал с daemon'ом.

    Возвращает суммарное количество удалённых событий по всем политикам.
    """
    policies = list_active(db)
    if not policies:
        return 0

    now = datetime.now(timezone.utc)
    total = 0
    for policy in policies:
        cutoff = now - timedelta(days=policy.retain_days)
        # Подзапрос отбирает id'ы под удаление пачкой; основной DELETE бьёт
        # ровно по этим первичным ключам. `IN (SELECT ... LIMIT n)` —
        # переносимый способ ограничить DELETE размером пачки.
        id_select = select(AuditEvent.id).where(
            AuditEvent.timestamp < cutoff,
            # Case-insensitive гард: блокирует bypass через 'LoGiNg_SeRvIcE',
            # 'LOGING_SERVICE' и т.п. (trailing whitespace — на ingest).
            func.lower(AuditEvent.service) != _PROTECTED_SERVICE,
        )
        if policy.severity is not None:
            id_select = id_select.where(AuditEvent.severity == policy.severity)
        if policy.service is not None:
            id_select = id_select.where(
                func.lower(AuditEvent.service) == policy.service.lower()
            )
        id_select = id_select.limit(chunk_size)

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
