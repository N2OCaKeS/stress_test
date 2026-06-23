"""Pydantic v2 схемы для эндпоинтов /credentials/{id}/acl."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class RoleACLCreate(BaseModel):
    """Тело POST /credentials/{id}/acl."""

    dept_id: str = Field(min_length=1, max_length=64)
    role_name: str = Field(min_length=1, max_length=64)
    can_read: bool = False
    can_write: bool = False


class RoleACLUpsert(BaseModel):
    """Тело PUT /credentials/{id}/acl.

    Задаёт желаемую пару флагов для (dept_id, role_name). Идемпотентно:
    строки нет — создаём, есть — переписываем; оба флага false — снимаем.
    """

    dept_id: str = Field(min_length=1, max_length=64)
    role_name: str = Field(min_length=1, max_length=64)
    can_read: bool = False
    can_write: bool = False


class RoleACLRead(BaseModel):
    id: str
    cred_id: str
    dept_id: str
    role_name: str
    can_read: bool
    can_write: bool
    granted_by_user_id: str
    granted_at: datetime


class RoleACLList(BaseModel):
    items: list[RoleACLRead]


class RoleACLUpsertResponse(BaseModel):
    """Ответ PUT /credentials/{id}/acl.

    `acl` — итоговая строка после апсерта; None, когда оба флага сняты и строка
    удалена («нет доступа»).
    """

    ok: bool = True
    acl: RoleACLRead | None = None
