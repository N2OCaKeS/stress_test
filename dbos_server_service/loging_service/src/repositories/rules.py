"""Репозиторий `AuditRule`."""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.core.config import get_settings
from src.models.audit_rule import AuditRule
from src.repositories.events import _with_statement_timeout
from src.schemas.rules import RuleCreate, RuleUpdate


def get_active_sorted(db: Session) -> list[AuditRule]:
    """Все активные не-удалённые правила, отсортированные по `priority DESC`."""
    stmt = (
        select(AuditRule)
        .where(AuditRule.is_active == True)  # noqa: E712
        .where(AuditRule.deleted_at.is_(None))
        .order_by(AuditRule.priority.desc())
    )
    return list(db.execute(stmt).scalars().all())


def get_max_updated_at(db: Session) -> datetime | None:
    """MAX(updated_at) по всем row, включая soft-deleted.

    Soft-delete (`deleted_at IS NOT NULL`) bump'ит `updated_at` той же строки,
    оставляя её в таблице. Кеш на других воркерах видит рост MAX и реагирует.
    Если бы мы фильтровали `deleted_at IS NULL`, MAX упал бы при удалении
    самой свежей строки — кеш не заметил бы изменения.
    """
    from sqlalchemy import func
    return db.execute(select(func.max(AuditRule.updated_at))).scalar()


def get_all(
    db: Session,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[AuditRule], int]:
    from sqlalchemy import func
    base = select(AuditRule).where(AuditRule.deleted_at.is_(None))
    count_stmt = select(func.count()).select_from(base.subquery())
    page_stmt = base.order_by(AuditRule.priority.desc()).offset(offset).limit(limit)

    # SELECT и COUNT идут под `SET LOCAL statement_timeout` — таблица правил
    # на порядок меньше audit-журнала, но read-канал всё равно должен иметь
    # верхнюю границу, чтобы один тяжёлый запрос (lock от долгой админ-сессии,
    # raised work_mem) не выжимал пул. Семантика на превышение симметрична
    # `events.query`: COUNT → 0 (page-нагрузка не падает), SELECT → пустая
    # страница + warning лог.
    timeout_ms = get_settings().audit_query_statement_timeout_ms
    if timeout_ms > 0:
        total = _with_statement_timeout(
            db,
            timeout_ms,
            lambda: db.execute(count_stmt).scalar_one(),
            on_canceled=lambda: 0,
            canceled_log_msg=(
                "rules COUNT exceeded statement_timeout=%dms; returning total=0"
            ),
        )
        rules = _with_statement_timeout(
            db,
            timeout_ms,
            lambda: list(db.execute(page_stmt).scalars()),
            on_canceled=lambda: [],
            canceled_log_msg=(
                "rules SELECT exceeded statement_timeout=%dms; returning empty page"
            ),
        )
    else:
        total = db.execute(count_stmt).scalar_one()
        rules = list(db.execute(page_stmt).scalars())
    return rules, total


def get_by_id(db: Session, rule_id: str) -> AuditRule | None:
    timeout_ms = get_settings().audit_query_statement_timeout_ms
    if timeout_ms > 0:
        rule = _with_statement_timeout(
            db,
            timeout_ms,
            lambda: db.get(AuditRule, rule_id),
            on_canceled=lambda: None,
            canceled_log_msg=(
                "rules GET-by-id exceeded statement_timeout=%dms; returning None"
            ),
        )
    else:
        rule = db.get(AuditRule, rule_id)
    if rule is None or rule.deleted_at is not None:
        return None
    return rule


def create(db: Session, payload: RuleCreate, *, commit: bool = True) -> AuditRule:
    """Создаёт правило. `commit=False` — для атомарного admin-CRUD,
    caller сам делает единственный `db.commit()` после CRUD + audit."""
    from src.utils.ids import audit_rule_id
    now = datetime.now(timezone.utc)
    rule = AuditRule(
        id=audit_rule_id(),
        name=payload.name,
        description=payload.description,
        is_active=payload.is_active,
        priority=payload.priority,
        match_service=payload.match_service,
        match_action=payload.match_action,
        match_status=payload.match_status,
        match_severity=payload.match_severity,
        match_allowed=payload.match_allowed,
        effect=payload.effect,
        effect_severity=payload.effect_severity,
        created_at=now,
        updated_at=now,
    )
    db.add(rule)
    if commit:
        db.commit()
        db.refresh(rule)
    else:
        db.flush()
    return rule


def update(
    db: Session, rule: AuditRule, payload: RuleUpdate, *, commit: bool = True
) -> AuditRule:
    """Partial-update правила. `commit=False` — атомарный admin-CRUD."""
    data = payload.model_dump(exclude_unset=True)
    for field, value in data.items():
        setattr(rule, field, value)
    rule.updated_at = datetime.now(timezone.utc)
    if commit:
        db.commit()
        db.refresh(rule)
    else:
        db.flush()
    return rule


def delete(db: Session, rule: AuditRule, *, commit: bool = True) -> None:
    """Soft-delete: ставит `deleted_at` и bump'ит `updated_at`.

    Физический DELETE не меняет MAX(updated_at) на оставшихся row — другие
    воркеры держат stale-кеш до TTL. Soft-delete оставляет строку, обновляя
    её timestamp, чтобы MAX(updated_at) переехал и cross-worker invalidation
    сработал штатно.

    `name` переименовываем (`<old>#deleted-<id>`), иначе UNIQUE constraint
    `uq_audit_rules_name` не даст создать правило с тем же именем повторно.

    `commit=False` — атомарный admin-CRUD: единственный commit делает endpoint
    после delete + audit.
    """
    now = datetime.now(timezone.utc)
    rule.deleted_at = now
    rule.updated_at = now
    rule.is_active = False
    rule.name = f"{rule.name}#deleted-{rule.id}"[:128]
    if commit:
        db.commit()
    else:
        db.flush()
