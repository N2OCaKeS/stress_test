"""Pydantic v2 схемы для эндпоинтов credentials."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator


_VALID_SCOPES = ("personal", "department", "cross_department")
_VALID_STATUS = ("active", "blocked")


class CredentialCreate(BaseModel):
    """Тело POST /credentials.

    Если `scope == personal`, owner — текущий identity (берётся в сервисе);
    `owner_dept_id` НЕ передаётся. Для department/cross_department `owner_dept_id`
    обязателен.
    """

    name: str = Field(min_length=1, max_length=64)
    service: str = Field(min_length=1, max_length=64)
    scope: Literal["personal", "department", "cross_department"]
    login: str | None = Field(default=None, max_length=4096)
    secret: str = Field(min_length=1, max_length=8192)
    owner_dept_id: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def _check_owner_dept_for_dept_scope(self) -> "CredentialCreate":
        if self.scope in {"department", "cross_department"} and not self.owner_dept_id:
            raise ValueError(
                f"owner_dept_id is required for scope={self.scope!r}"
            )
        if self.scope == "personal" and self.owner_dept_id is not None:
            raise ValueError(
                "owner_dept_id must be empty for scope='personal'"
            )
        return self


class CredentialUpdate(BaseModel):
    """Тело PATCH /credentials/{id}. Все поля optional, partial update."""

    name: str | None = Field(default=None, min_length=1, max_length=64)
    login: str | None = Field(default=None, max_length=4096)
    secret: str | None = Field(default=None, min_length=1, max_length=8192)


class CredentialRead(BaseModel):
    """Метаданные кред — без plaintext-secret и без `secret_encrypted`."""

    id: str
    name: str
    service: str
    scope: Literal["personal", "department", "cross_department"]
    owner_user_id: str | None
    owner_dept_id: str | None
    login: str | None
    status: Literal["active", "blocked"]
    created_by: str
    created_at: datetime
    updated_at: datetime
    blocked_at: datetime | None = None
    blocked_reason: str | None = None


class CredentialList(BaseModel):
    """Cursor-пагинированный список кред."""

    items: list[CredentialRead]
    next_cursor: str | None = None


class CredentialRevealResponse(BaseModel):
    """Ответ POST /credentials/{id}/reveal. `secret_b64` — base64(plaintext)."""

    login: str | None
    secret_b64: str


class CredentialBlockedResponse(BaseModel):
    """410 GONE shape для заблокированных кред."""

    error: str = "gone"
    error_code: str = "CREDENTIAL_BLOCKED"
    message: str
    blocked_reason: str | None = None
    blocked_at: datetime | None = None


class TransferRequest(BaseModel):
    """Тело POST /credentials/{id}/transfer.

    Ровно одно из полей должно быть задано. Сервис проверяет совместимость
    с scope кред'ы.
    """

    new_owner_user_id: str | None = Field(default=None, max_length=64)
    new_owner_dept_id: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def _exactly_one_owner(self) -> "TransferRequest":
        if bool(self.new_owner_user_id) == bool(self.new_owner_dept_id):
            raise ValueError(
                "exactly one of new_owner_user_id / new_owner_dept_id must be set"
            )
        return self


class AdminDeleteRequest(BaseModel):
    """Тело DELETE /credentials/{id} для admin override.

    Если делает не-owner (через service_admin), `reason` обязателен.
    """

    reason: str | None = Field(default=None, min_length=1, max_length=256)
