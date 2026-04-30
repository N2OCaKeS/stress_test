"""RetentionPolicy repository — single global retention configuration."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from src.models.audit_event import AuditEvent
from src.models.retention_policy import RetentionPolicy
from src.schemas.retention import RetentionPolicyCreate, RetentionPolicyUpdate

_PROTECTED_SERVICE = "loging_service"


def get_active(db: Session) -> RetentionPolicy | None:
    """Return the single active retention policy (most recently created)."""
    return db.execute(
        select(RetentionPolicy)
        .where(RetentionPolicy.is_active == True)  # noqa: E712
        .order_by(RetentionPolicy.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def get_by_id(db: Session, policy_id: str) -> RetentionPolicy | None:
    return db.get(RetentionPolicy, policy_id)


def create(db: Session, payload: RetentionPolicyCreate) -> RetentionPolicy:
    now = datetime.now(timezone.utc)
    policy = RetentionPolicy(
        severity=None,
        service=None,
        retain_days=payload.retain_days,
        description=payload.description,
        is_active=payload.is_active,
        created_at=now,
        updated_at=now,
    )
    db.add(policy)
    db.commit()
    db.refresh(policy)
    return policy


def update(db: Session, policy: RetentionPolicy, payload: RetentionPolicyUpdate) -> RetentionPolicy:
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(policy, field, value)
    policy.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(policy)
    return policy


def delete_policy(db: Session, policy: RetentionPolicy) -> None:
    db.delete(policy)
    db.commit()


def apply_active(db: Session) -> int:
    """Delete all events older than retain_days, except loging_service events.

    Returns number of deleted events. Does nothing if no active policy exists.
    """
    policy = get_active(db)
    if not policy:
        return 0

    cutoff = datetime.now(timezone.utc) - timedelta(days=policy.retain_days)
    result = db.execute(
        delete(AuditEvent)
        .where(AuditEvent.timestamp < cutoff)
        .where(AuditEvent.service != _PROTECTED_SERVICE)
    )
    db.commit()
    return result.rowcount
