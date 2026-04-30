"""Retention policy — global log retention configuration. Requires loging_admin.

One active policy applies to ALL audit events.
loging_service events are NEVER deleted (protected from rotation for security).

Background cleanup runs automatically every hour.
Use POST /retention/apply for immediate execution.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from src.dependencies.auth import require_admin
from src.dependencies.db import get_db
from src.repositories import retention_policies as repo
from src.schemas.retention import (
    RetentionPolicyCreate,
    RetentionPolicyResponse,
    RetentionPolicyUpdate,
)

router = APIRouter(dependencies=[Depends(require_admin)])


@router.get(
    "",
    response_model=RetentionPolicyResponse | None,
    summary="Get current retention policy",
    description="Returns the active retention policy, or null if none is configured.",
)
def get_policy(db: Session = Depends(get_db)) -> RetentionPolicyResponse | None:
    policy = repo.get_active(db)
    return RetentionPolicyResponse.model_validate(policy) if policy else None


@router.put(
    "",
    response_model=RetentionPolicyResponse,
    summary="Set retention policy",
    description=(
        "Creates or replaces the active retention policy. "
        "All audit events older than retain_days will be deleted during the next cleanup. "
        "loging_service events are always exempt from deletion."
    ),
)
def set_policy(
    payload: RetentionPolicyCreate,
    db: Session = Depends(get_db),
) -> RetentionPolicyResponse:
    existing = repo.get_active(db)
    if existing:
        updated = repo.update(db, existing, RetentionPolicyUpdate(
            retain_days=payload.retain_days,
            description=payload.description,
            is_active=payload.is_active,
        ))
        return RetentionPolicyResponse.model_validate(updated)
    policy = repo.create(db, payload)
    return RetentionPolicyResponse.model_validate(policy)


@router.delete(
    "",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Disable retention (keep events forever)",
)
def disable_policy(db: Session = Depends(get_db)) -> None:
    policy = repo.get_active(db)
    if policy:
        repo.update(db, policy, RetentionPolicyUpdate(is_active=False))


