"""Pydantic v2 схемы для эндпоинтов /credentials/{id}/dept-grants."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class DeptGrantCreate(BaseModel):
    """Тело POST /credentials/{id}/dept-grants."""

    recipient_dept_id: str = Field(min_length=1, max_length=64)


class DeptGrantRead(BaseModel):
    id: str
    cred_id: str
    recipient_dept_id: str
    granted_by_user_id: str
    granted_at: datetime


class DeptGrantList(BaseModel):
    items: list[DeptGrantRead]
