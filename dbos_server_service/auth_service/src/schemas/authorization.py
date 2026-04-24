"""Schemas for introspection and service access checks."""

from pydantic import BaseModel, Field


class IntrospectRequest(BaseModel):
    token: str


class IntrospectResponse(BaseModel):
    active: bool
    subject_type: str | None = None  # "user" | "bot"
    sub: str | None = None
    department_id: str | None = None
    allowed_services: list[str] = Field(default_factory=list)
    service_roles: dict[str, list[str]] = Field(default_factory=dict)
    exp: int | None = None


class ServiceAccessRequest(BaseModel):
    subject_token: str
    service_name: str


class ServiceAccessResponse(BaseModel):
    allowed: bool
    department_id: str | None = None
    service_roles: list[str] = Field(default_factory=list)
