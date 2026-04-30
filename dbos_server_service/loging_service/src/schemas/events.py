"""Request/response schemas for audit events."""

from datetime import datetime
from typing import Literal

import json

from pydantic import BaseModel, Field, field_validator


class EventCreate(BaseModel):
    """Payload sent by a service to record an audit event."""

    timestamp: datetime = Field(description="When the event occurred (ISO 8601 with tz)")
    service: str = Field(max_length=64, description="Originating service name")
    action: str = Field(max_length=128, description="Dot-namespaced action, e.g. 'user.login'")

    actor_id: str | None = Field(default=None, max_length=48)
    actor_type: Literal["user", "bot", "service", "anonymous"] = Field(default="user")
    username: str | None = Field(default=None, max_length=128, description="Human-readable actor name")
    department_id: str | None = Field(default=None, max_length=48)

    target_id: str | None = Field(default=None, max_length=48)
    target_type: str | None = Field(default=None, max_length=64)

    status: Literal["success", "failure", "denied"] = Field(description="Outcome of the action")
    allowed: bool = Field(description="Whether the action was authorised")
    severity: Literal["TRACE", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] | None = Field(
        default=None,
        description="Importance of the event. If omitted, logging_service resolves it from defaults and rules.",
    )

    request_id: str | None = Field(default=None, max_length=64)
    details: dict = Field(
        default_factory=dict,
        description="Technical context — must not contain secrets or passwords",
    )

    @field_validator("details")
    @classmethod
    def _details_size(cls, v: dict) -> dict:
        if len(json.dumps(v, default=str)) > 65_536:
            raise ValueError("details must not exceed 64 KB")
        return v


class EventResponse(BaseModel):
    """Returned after a successful event ingestion."""

    id: str
    received_at: datetime

    model_config = {"from_attributes": True}


class EventDetail(BaseModel):
    """Full event record returned by query endpoints."""

    id: str
    timestamp: datetime
    received_at: datetime
    service: str
    action: str
    actor_id: str | None
    actor_type: str
    username: str | None
    department_id: str | None
    target_id: str | None
    target_type: str | None
    status: str
    allowed: bool
    severity: str
    request_id: str | None
    details: dict

    model_config = {"from_attributes": True}


class EventListResponse(BaseModel):
    """Paginated list of audit events."""

    items: list[EventDetail]
    total: int
    limit: int
    offset: int
