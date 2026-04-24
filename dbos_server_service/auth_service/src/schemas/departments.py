"""Schemas for department management and service access."""

from datetime import datetime

from pydantic import BaseModel


class DepartmentCreate(BaseModel):
    name: str
    display_name: str


class DepartmentResponse(BaseModel):
    department_id: str
    name: str
    display_name: str
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class GrantServiceAccessRequest(BaseModel):
    service_name: str


class ServiceAccessResponse(BaseModel):
    department_id: str
    service_name: str
    enabled: bool
    granted_at: datetime | None = None
