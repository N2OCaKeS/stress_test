"""Schemas for retention policy management."""

from datetime import datetime

from pydantic import BaseModel, Field


class RetentionPolicyCreate(BaseModel):
    retain_days: int = Field(
        ge=30,
        le=3650,
        description="How many days to keep ALL audit events (minimum 30 days). loging_service events are always protected.",
    )
    description: str | None = Field(default=None, max_length=256)
    is_active: bool = Field(default=True)


class RetentionPolicyUpdate(BaseModel):
    retain_days: int | None = Field(default=None, ge=30, le=3650)
    description: str | None = None
    is_active: bool | None = None


class RetentionPolicyResponse(BaseModel):
    id: str
    retain_days: int
    description: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class RetentionApplyResponse(BaseModel):
    deleted: int
    message: str
