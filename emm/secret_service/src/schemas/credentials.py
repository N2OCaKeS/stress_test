"""Pydantic v2 схемы для эндпоинтов credentials."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field, PrivateAttr, model_validator

from src.core.b64 import decode_b64

# Максимум plaintext-секрета после декода (символы). base64 раздувает на ~4/3,
# поэтому сырое поле допускаем шире — точную границу даёт проверка plaintext.
_SECRET_MAX_PLAINTEXT = 8192
_SECRET_B64_MAX = 16384


def _decode_secret_field(value: str) -> str:
    """base64 → plaintext с проверкой длины 1..8192 по раскодированному."""
    plaintext = decode_b64(value, "secret_b64")
    if len(plaintext) < 1:
        raise ValueError("secret_b64 decodes to an empty secret")
    if len(plaintext) > _SECRET_MAX_PLAINTEXT:
        raise ValueError(
            f"decoded secret exceeds {_SECRET_MAX_PLAINTEXT} characters"
        )
    return plaintext


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _to_utc(value: datetime) -> datetime:
    """Naive datetime трактуем как UTC; aware — конвертим в UTC.

    Сравнения valid_from/valid_to идут в UTC; иначе naive vs aware datetime
    кидает TypeError.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class CredentialCreate(BaseModel):
    """Тело POST /credentials.

    Если `scope == personal`, owner — текущий identity (берётся в сервисе);
    `owner_dept_id` НЕ передаётся. Для department/cross_department/service
    `owner_dept_id` обязателен — `service` физически принадлежит отделу
    точно так же, как `department` (см. `access_service._check_service` для
    read/reveal-исключения в пользу платформенных сервис-ботов).
    """

    name: str = Field(min_length=1, max_length=64)
    service: str = Field(min_length=1, max_length=64)
    scope: Literal["personal", "department", "cross_department", "service"]
    login: str | None = Field(default=None, max_length=4096)
    secret_b64: str = Field(
        min_length=1,
        max_length=_SECRET_B64_MAX,
        description="base64(plaintext-secret); клиент кодирует base64.b64encode(plaintext). Декодированный секрет — 1..8192 символов UTF-8.",
    )
    owner_dept_id: str | None = Field(default=None, max_length=64)
    visible_to_dept: bool = False
    # Окно валидности секрета (UTC). NULL = open-ended с этой стороны.
    # valid_from в прошлом разрешён (=> сразу активен); valid_to обязан быть
    # в будущем — создавать уже-expired кред'у нельзя (зачем?).
    valid_from: datetime | None = None
    valid_to: datetime | None = None

    _secret: str = PrivateAttr()

    @property
    def secret(self) -> str:
        """Раскодированный plaintext-секрет (декод происходит один раз)."""
        return self._secret

    @model_validator(mode="after")
    def _decode_secret(self) -> "CredentialCreate":
        self._secret = _decode_secret_field(self.secret_b64)
        return self

    @model_validator(mode="after")
    def _check_owner_dept_for_dept_scope(self) -> "CredentialCreate":
        if self.scope in {"department", "cross_department", "service"} and not self.owner_dept_id:
            raise ValueError(
                f"owner_dept_id is required for scope={self.scope!r}"
            )
        if self.scope == "personal" and self.owner_dept_id is not None:
            raise ValueError(
                "owner_dept_id must be empty for scope='personal'"
            )
        return self

    @model_validator(mode="after")
    def _check_validity_window(self) -> "CredentialCreate":
        if self.valid_from is not None and self.valid_to is not None:
            if _to_utc(self.valid_to) <= _to_utc(self.valid_from):
                raise ValueError("valid_to must be strictly greater than valid_from")
        if self.valid_to is not None and _to_utc(self.valid_to) <= _now_utc():
            raise ValueError("valid_to must be in the future (refuse to create already-expired credential)")
        return self


class CredentialUpdate(BaseModel):
    """Тело PATCH /credentials/{id}. Все поля optional, partial update.

    `valid_from` / `valid_to` — admin может продлевать срок токена; для отмены
    окна с какой-то стороны юзеру придётся пересоздавать креду (NULL через
    PATCH намеренно не маппим, иначе случайный `{"valid_to": null}` снимет
    защиту).
    """

    name: str | None = Field(default=None, min_length=1, max_length=64)
    login: str | None = Field(default=None, max_length=4096)
    secret_b64: str | None = Field(
        default=None,
        min_length=1,
        max_length=_SECRET_B64_MAX,
        description="base64(plaintext-secret) или None (не менять). Декодированный секрет — 1..8192 символов UTF-8.",
    )
    # Гейт guest-видимости общей кред'ы. None = не менять.
    visible_to_dept: bool | None = None
    valid_from: datetime | None = None
    valid_to: datetime | None = None

    _secret: str | None = PrivateAttr(default=None)

    @property
    def secret(self) -> str | None:
        """Раскодированный plaintext-секрет либо None (декод один раз)."""
        return self._secret

    @model_validator(mode="after")
    def _decode_secret(self) -> "CredentialUpdate":
        if self.secret_b64 is not None:
            self._secret = _decode_secret_field(self.secret_b64)
        return self

    @model_validator(mode="after")
    def _check_validity_window(self) -> "CredentialUpdate":
        if self.valid_from is not None and self.valid_to is not None:
            if _to_utc(self.valid_to) <= _to_utc(self.valid_from):
                raise ValueError("valid_to must be strictly greater than valid_from")
        return self


class CredentialRead(BaseModel):
    """Метаданные кред — без plaintext-secret и без `secret_encrypted`."""

    id: str
    name: str
    service: str
    scope: Literal["personal", "department", "cross_department", "service"]
    owner_user_id: str | None
    owner_dept_id: str | None
    login: str | None
    status: Literal["active", "blocked"]
    created_by: str
    created_at: datetime
    updated_at: datetime
    blocked_at: datetime | None = None
    blocked_reason: str | None = None
    visible_to_dept: bool = False
    valid_from: datetime | None = None
    valid_to: datetime | None = None


class CredentialGuestRead(BaseModel):
    """Метаданные кред для guest-роли (уровень `view`).

    Guest видит наличие и метаданные dept-секрета (id, name, service, scope,
    login, visible_to_dept), но не значение (нет secret/secret_encrypted) и не
    служебные поля (owner_*/created_by/timestamps/blocked-поля).
    """

    id: str
    name: str
    service: str
    scope: Literal["personal", "department", "cross_department", "service"]
    login: str | None = None
    visible_to_dept: bool


class CredentialList(BaseModel):
    """Cursor-пагинированный список кред."""

    items: list[CredentialRead]
    next_cursor: str | None = None


class CredentialGuestList(BaseModel):
    """Cursor-пагинированный список кред для guest-роли."""

    items: list[CredentialGuestRead]
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

    Ровно одно из owner-полей должно быть задано. `reason` обязателен, как и у
    admin_override_delete — transfer считается CRITICAL-операцией и след в
    аудите без причины бесполезен.
    """

    new_owner_user_id: str | None = Field(default=None, max_length=64)
    new_owner_dept_id: str | None = Field(default=None, max_length=64)
    reason: str = Field(min_length=1, max_length=256)

    @model_validator(mode="after")
    def _exactly_one_owner(self) -> "TransferRequest":
        if bool(self.new_owner_user_id) == bool(self.new_owner_dept_id):
            raise ValueError(
                "exactly one of new_owner_user_id / new_owner_dept_id must be set"
            )
        return self


class AdminDeleteRequest(BaseModel):
    """Тело DELETE /credentials/{id} для admin override.

    Если делает не-owner (admin secret_service своего dept'а или account_admin),
    `reason` обязателен.
    """

    reason: str | None = Field(default=None, min_length=1, max_length=256)
