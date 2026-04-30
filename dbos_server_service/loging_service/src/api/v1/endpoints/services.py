"""Service registry endpoints.

POST /services/{service}/events  — service registers its events (SERVICE_API_KEY)
GET  /services                   — admin views (loging_admin JWT)
GET  /services/{service}/events  — admin views (loging_admin JWT)
"""

from fastapi import APIRouter, Depends, Path, Query, status
from sqlalchemy.orm import Session

from src.dependencies.auth import AdminIdentity, ReaderIdentity, require_service_token
from src.dependencies.db import get_db
from src.repositories import events as events_repo
from src.repositories import service_events as se_repo
from src.schemas.services import (
    RegisterEventsRequest,
    RegisterEventsResponse,
    ServiceEventsResponse,
    ServiceEventDetail,
    ServiceInfo,
    ServiceListResponse,
)

router = APIRouter()


@router.get(
    "",
    response_model=ServiceListResponse,
    summary="List services that have sent audit events",
)
def list_services(
    identity: ReaderIdentity,
    db: Session = Depends(get_db),
) -> ServiceListResponse:
    rows = events_repo.list_services(db)
    items = [
        ServiceInfo(service=r.service, event_count=r.event_count, last_event_at=r.last_event_at)
        for r in rows
    ]
    return ServiceListResponse(items=items, total=len(items))


@router.post(
    "/{service}/events",
    response_model=RegisterEventsResponse,
    status_code=status.HTTP_200_OK,
    summary="Register (upsert) the event list for a service",
    dependencies=[Depends(require_service_token)],
)
def register_events(
    service: str = Path(description="Service name, e.g. 'auth_service'"),
    payload: RegisterEventsRequest = ...,
    db: Session = Depends(get_db),
) -> RegisterEventsResponse:
    events_list = [ev.model_dump() for ev in payload.events]
    added, updated = se_repo.upsert_events(db, service, events_list)
    _, total = se_repo.list_for_service(db, service)
    return RegisterEventsResponse(service=service, added=added, updated=updated, total=total)


@router.get(
    "/{service}/events",
    response_model=ServiceEventsResponse,
    summary="List registered events for a service",
)
def list_service_events(
    identity: ReaderIdentity,
    service: str = Path(description="Service name, e.g. 'auth_service'"),
    db: Session = Depends(get_db),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> ServiceEventsResponse:
    rows, total = se_repo.list_for_service(db, service, limit=limit, offset=offset)
    return ServiceEventsResponse(
        service=service,
        items=[ServiceEventDetail.model_validate(r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )
