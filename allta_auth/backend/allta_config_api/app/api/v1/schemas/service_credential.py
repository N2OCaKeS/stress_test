from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class ServiceCredentialCreate(BaseModel):
    service_name: str = Field(..., min_length=1, max_length=120)
    username: str = Field(..., min_length=1, max_length=255)
    password: str = Field(..., min_length=1, max_length=255)


class ServiceCredentialUpdate(BaseModel):
    username: str | None = Field(None, min_length=1, max_length=255)
    password: str | None = Field(None, min_length=1, max_length=255)


class ServiceCredentialRead(BaseModel):
    id: int
    service_name: str
    username: str
    password: str
    updated_by: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
