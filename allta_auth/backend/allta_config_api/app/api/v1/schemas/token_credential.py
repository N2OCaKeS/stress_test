from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class TokenCredentialCreate(BaseModel):
    token_key: str = Field(..., min_length=1, max_length=120)
    token: str = Field(..., min_length=1, max_length=4096)


class TokenCredentialUpdate(BaseModel):
    token: str = Field(..., min_length=1, max_length=4096)


class TokenCredentialRead(BaseModel):
    id: int
    token_key: str
    token: str
    updated_by: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
