"""Schemas for the /services endpoints."""

from datetime import datetime

from pydantic import BaseModel, Field


# ── Service summary (from audit_events) ──────────────────────────────────────

class ServiceInfo(BaseModel):
    service: str
    event_count: int
    last_event_at: datetime


class ServiceListResponse(BaseModel):
    items: list[ServiceInfo]
    total: int


# ── Service event registry ────────────────────────────────────────────────────

class EventDefinition(BaseModel):
    """A single event that a service declares it can emit."""

    action: str = Field(
        max_length=128,
        description="Dot-namespaced action name, e.g. 'user.login'",
    )
    description: str | None = Field(
        default=None,
        max_length=256,
        description="Human-readable description of when this event occurs",
    )
    default_severity: str | None = Field(
        default=None,
        description="Suggested default severity (TRACE/DEBUG/INFO/WARNING/ERROR/CRITICAL)",
    )


class RegisterEventsRequest(BaseModel):
    events: list[EventDefinition] = Field(
        min_length=1,
        description="Full list of events this service can emit",
    )


class RegisterEventsResponse(BaseModel):
    service: str
    added: int
    updated: int
    total: int


class ServiceEventDetail(BaseModel):
    """Registered event as stored in service_events table."""

    action: str
    description: str | None
    default_severity: str | None
    registered_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ServiceEventsResponse(BaseModel):
    service: str
    items: list[ServiceEventDetail]
    total: int
    limit: int
    offset: int
