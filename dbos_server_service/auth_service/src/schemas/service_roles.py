"""Schemas for service role definitions."""

from datetime import datetime

from pydantic import BaseModel


class ServiceRoleCreate(BaseModel):
    role_name: str
    display_name: str
    description: str | None = None


class ServiceRoleUpdate(BaseModel):
    display_name: str | None = None
    description: str | None = None


class ServiceRoleResponse(BaseModel):
    id: str
    service_name: str
    role_name: str
    display_name: str
    description: str | None
    is_active: bool
    created_at: datetime
    created_by: str | None

    model_config = {"from_attributes": True}
