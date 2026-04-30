"""Audit event ingestion and query endpoints.

POST /events  — service writes (SERVICE_API_KEY)
GET  /events  — loging_admin | loging_reader | department_admin | account_admin
               department_admin and loging_reader are auto-scoped to their department
"""

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from sqlalchemy.orm import Session

from src.dependencies.auth import ReaderIdentity, require_service_token
from src.dependencies.db import get_db
from src.schemas.events import EventCreate, EventListResponse, EventResponse
from src.services import event_service

router = APIRouter()

_MAX_LIMIT = 1000


@router.post(
    "",
    response_model=EventResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Ingest a single audit event",
    responses={204: {"description": "Event suppressed by audit rule"}},
    dependencies=[Depends(require_service_token)],
)
def create_event(
    payload: EventCreate,
    db: Session = Depends(get_db),
) -> Response | EventResponse:
    event = event_service.record(db, payload)
    if event is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    return EventResponse.model_validate(event)


@router.get(
    "",
    response_model=EventListResponse,
    summary="Query audit events with optional filters",
    description=(
        "Access: loging_admin and account_admin see all events. "
        "loging_reader and department_admin are auto-scoped to their department. "
        "Results are sorted by timestamp descending."
    ),
)
def list_events(
    identity: ReaderIdentity,
    db: Session = Depends(get_db),
    department_id: str | None = Query(default=None, description="Filter by department ID"),
    service: str | None = Query(default=None, description="Filter by originating service name"),
    severity: Literal["TRACE", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] | None = Query(default=None),
    action: str | None = Query(default=None, description="Filter by action name (exact match)"),
    from_time: datetime | None = Query(default=None, description="Start of time range (ISO 8601)"),
    to_time: datetime | None = Query(default=None, description="End of time range (ISO 8601)"),
    limit: int = Query(default=100, ge=1, le=_MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
) -> EventListResponse:
    # Department scoping: if user is scoped, enforce it
    dept_scope = identity.get("_dept_scope")
    if dept_scope:
        if department_id and department_id != dept_scope:
            raise HTTPException(
                status_code=403,
                detail=f"Access restricted to department '{dept_scope}'",
            )
        department_id = dept_scope

    return event_service.query(
        db,
        department_id=department_id,
        service=service,
        severity=severity,
        action=action,
        from_time=from_time,
        to_time=to_time,
        limit=limit,
        offset=offset,
    )
