"""Pydantic v2 схемы для эндпоинтов /credentials/{id}/user-acl."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class UserACLCreate(BaseModel):
    """Тело POST /credentials/{id}/user-acl.

    `user_id` — кому выдаём доступ (в UI выбирается по username, резолвится в
    user_id до вызова). `can_read` по умолчанию True: выдача доступа без права
    чтения смысла не имеет, но оставляем явным флагом ради симметрии с RoleACL.
    """

    user_id: str = Field(min_length=1, max_length=64)
    can_read: bool = True
    can_write: bool = False


class UserACLOut(BaseModel):
    id: str
    cred_id: str
    user_id: str
    can_read: bool
    can_write: bool
    granted_by_user_id: str
    created_at: datetime


class UserACLList(BaseModel):
    items: list[UserACLOut]
