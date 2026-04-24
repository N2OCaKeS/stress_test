"""Schemas for personal access tokens."""

from datetime import datetime

from pydantic import BaseModel, Field


class PATCreate(BaseModel):
    name: str = Field(min_length=1, max_length=256)
    expires_at: datetime | None = None
    allowed_services: list[str] = Field(default_factory=list)


class PATCreateResponse(BaseModel):
    """Returned once at creation — contains the raw token value."""
    token_id: str
    token: str
    name: str
    expires_at: datetime | None


class PATListItem(BaseModel):
    token_id: str
    name: str
    token_prefix: str
    allowed_services: list[str]
    created_at: datetime
    expires_at: datetime | None
    last_used_at: datetime | None
    revoked_at: datetime | None

    model_config = {"from_attributes": True}
