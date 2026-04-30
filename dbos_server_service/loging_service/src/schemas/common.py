"""Shared response schemas."""

from pydantic import BaseModel


class ErrorResponse(BaseModel):
    error: str
    error_code: str
    message: str
    details: dict = {}
    request_id: str | None = None
    timestamp: str
