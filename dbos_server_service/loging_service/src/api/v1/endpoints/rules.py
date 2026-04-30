"""Audit rule management endpoints. Requires platform_role=loging_admin.

All create/update/delete actions are recorded unconditionally (bypass_rules)
so an admin can never accidentally suppress their own audit trail.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.dependencies.auth import AdminIdentity, ReaderIdentity, require_admin
from src.dependencies.db import get_db
from src.repositories import rules as rule_repo
from src.repositories import service_events as se_repo
from src.schemas.events import EventCreate
from src.schemas.rules import RuleCreate, RuleListResponse, RuleResponse, RuleUpdate
from src.services import event_service, rule_service

router = APIRouter()


def _get_or_404(db: Session, rule_id: str):
    rule = rule_repo.get_by_id(db, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail=f"Правило '{rule_id}' не найдено")
    return rule


def _audit(db: Session, identity: dict, action: str, details: dict) -> None:
    """Record an admin action unconditionally (bypasses suppression rules)."""
    event_service.record_admin_action(
        db,
        EventCreate(
            timestamp=datetime.now(timezone.utc),
            service="loging_service",
            action=action,
            actor_id=identity.get("user_id"),
            actor_type="user",
            status="success",
            allowed=True,
            severity=None,
            details=details,
        ),
    )


@router.get("", response_model=RuleListResponse, summary="Список всех правил аудита")
def list_rules(
    identity: ReaderIdentity,
    db: Session = Depends(get_db),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> RuleListResponse:
    rules, total = rule_repo.get_all(db, limit=limit, offset=offset)
    return RuleListResponse(
        items=[RuleResponse.model_validate(r) for r in rules],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{rule_id}", response_model=RuleResponse, summary="Получить правило по ID")
def get_rule(rule_id: str, identity: ReaderIdentity, db: Session = Depends(get_db)) -> RuleResponse:
    return RuleResponse.model_validate(_get_or_404(db, rule_id))


@router.post(
    "",
    response_model=RuleResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Создать правило аудита",
    dependencies=[Depends(require_admin)],
)
def create_rule(
    payload: RuleCreate,
    identity: AdminIdentity,
    db: Session = Depends(get_db),
) -> RuleResponse:
    _validate_match_action(payload.match_action, db)
    try:
        rule = rule_repo.create(db, payload)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409, detail=f"Правило с именем '{payload.name}' уже существует"
        )
    rule_service.invalidate_cache()
    _audit(db, identity, "logging_rule.create", {"rule_id": rule.id, "rule_name": rule.name})
    return RuleResponse.model_validate(rule)


@router.patch("/{rule_id}", response_model=RuleResponse, summary="Обновить правило",
              dependencies=[Depends(require_admin)])
def update_rule(
    rule_id: str,
    payload: RuleUpdate,
    identity: AdminIdentity,
    db: Session = Depends(get_db),
) -> RuleResponse:
    _validate_match_action(payload.match_action, db)
    rule = _get_or_404(db, rule_id)
    new_effect = payload.effect if payload.effect is not None else rule.effect
    new_effect_severity = (
        payload.effect_severity
        if "effect_severity" in payload.model_fields_set
        else rule.effect_severity
    )
    if new_effect == "OVERRIDE_SEVERITY" and not new_effect_severity:
        raise HTTPException(
            status_code=422, detail="effect_severity обязателен при effect=OVERRIDE_SEVERITY"
        )
    updated = rule_repo.update(db, rule, payload)
    rule_service.invalidate_cache()
    _audit(db, identity, "logging_rule.update", {"rule_id": rule_id, "changes": payload.model_dump(exclude_unset=True)})
    return RuleResponse.model_validate(updated)


@router.delete("/{rule_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Удалить правило",
               dependencies=[Depends(require_admin)])
def delete_rule(
    rule_id: str,
    identity: AdminIdentity,
    db: Session = Depends(get_db),
) -> None:
    rule = _get_or_404(db, rule_id)
    _audit(db, identity, "logging_rule.delete", {"rule_id": rule_id, "rule_name": rule.name})
    rule_repo.delete(db, rule)
    rule_service.invalidate_cache()


def _validate_match_action(match_action: str | None, db: Session) -> None:
    if match_action is None or "*" in match_action:
        return
    if not se_repo.action_is_registered(db, match_action):
        if se_repo.list_all(db):
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Action '{match_action}' is not registered by any service. "
                    "Use GET /services/{{service}}/events to see available actions, "
                    "or use a glob pattern (e.g. 'user.*')."
                ),
            )
