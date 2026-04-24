"""Common response and error schemas."""

from datetime import datetime

from pydantic import BaseModel, Field


class ErrorResponse(BaseModel):
    error: str
    error_code: str
    message: str
    details: dict = Field(default_factory=dict)
    request_id: str | None = None
    timestamp: datetime | None = None


class OkResponse(BaseModel):
    ok: bool = True
