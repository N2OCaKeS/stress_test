"""Schemas for bot accounts and bot tokens."""

from datetime import datetime

from pydantic import BaseModel, Field


class BotCreate(BaseModel):
    name: str = Field(min_length=1, max_length=256)
    department_id: str
    allowed_services: list[str] = Field(default_factory=list)
    description: str | None = None


class BotUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    status: str | None = None
    allowed_services: list[str] | None = None


class BotResponse(BaseModel):
    bot_id: str
    name: str
    department_id: str
    allowed_services: list[str]
    description: str | None
    status: str
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class BotTokenCreate(BaseModel):
    name: str = Field(min_length=1, max_length=256)
    expires_at: datetime | None = None


class BotTokenCreateResponse(BaseModel):
    """Returned once at creation — contains the raw token value."""
    token_id: str
    token: str
    name: str
    expires_at: datetime | None


class BotTokenListItem(BaseModel):
    token_id: str
    name: str
    token_prefix: str
    created_at: datetime
    expires_at: datetime | None
    last_used_at: datetime | None
    revoked_at: datetime | None

    model_config = {"from_attributes": True}
