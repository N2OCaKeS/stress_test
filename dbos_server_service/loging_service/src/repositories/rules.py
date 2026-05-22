"""Репозиторий `AuditRule`."""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.models.audit_rule import AuditRule
from src.schemas.rules import RuleCreate, RuleUpdate


def get_active_sorted(db: Session) -> list[AuditRule]:
    """Все активные правила, отсортированные по `priority DESC`."""
    stmt = (
        select(AuditRule)
        .where(AuditRule.is_active == True)  # noqa: E712
        .order_by(AuditRule.priority.desc())
    )
    return list(db.execute(stmt).scalars().all())


def get_max_updated_at(db: Session) -> datetime | None:
    from sqlalchemy import func
    return db.execute(select(func.max(AuditRule.updated_at))).scalar()


def get_all(
    db: Session,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[AuditRule], int]:
    from sqlalchemy import func
    total = db.execute(select(func.count()).select_from(AuditRule)).scalar_one()
    rules = list(
        db.execute(
            select(AuditRule).order_by(AuditRule.priority.desc()).offset(offset).limit(limit)
        ).scalars()
    )
    return rules, total


def get_by_id(db: Session, rule_id: str) -> AuditRule | None:
    return db.get(AuditRule, rule_id)


def create(db: Session, payload: RuleCreate) -> AuditRule:
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
    db.commit()
    db.refresh(rule)
    return rule


def update(db: Session, rule: AuditRule, payload: RuleUpdate) -> AuditRule:
    data = payload.model_dump(exclude_unset=True)
    for field, value in data.items():
        setattr(rule, field, value)
    rule.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(rule)
    return rule


def delete(db: Session, rule: AuditRule) -> None:
    db.delete(rule)
    db.commit()
