"""Schemas for platform service registry."""

from datetime import datetime

from pydantic import BaseModel


class ServiceCreate(BaseModel):
    service_name: str
    display_name: str
    description: str | None = None


class ServiceResponse(BaseModel):
    service_name: str
    display_name: str
    description: str | None
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}
