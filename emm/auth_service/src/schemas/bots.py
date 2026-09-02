"""Схемы для bot accounts и bot tokens."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class BotCreate(BaseModel):
    """Тело `POST /bots` — создание service-account.

    `extra="forbid"` стоит точечно: остальные схемы в auth permissive,
    но конкретно тут раньше пропускали `service_roles` через тело и
    тихо его теряли (роль не назначалась, а 201 уходил). Лучше явно
    422, чем молчаливый дроп.
    """
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=256, description="Имя бота (глобально уникально — docker basic-auth ищет по нему).")
    department_id: str = Field(description="ID отдела, к которому привязываем бота.")
    allowed_services: list[str] = Field(
        default_factory=list,
        description="Белый список сервисов, куда бот сможет ходить через свои токены.",
    )
    description: str | None = Field(default=None, description="Произвольное описание.")


class BotUpdate(BaseModel):
    """Тело `PATCH /bots/{bot_id}` — частичный апдейт.

    Активность бота меняется через `status` ("active"/"disabled"); `is_active`
    тут отдельным полем не принимается (схема permissive — лишний ключ молча
    игнорируется, апдейт пройдёт как no-op).
    """
    name: str | None = None
    description: str | None = None
    status: Literal["active", "disabled"] | None = Field(
        default=None,
        description='Статус бота: "active" или "disabled".',
    )
    allowed_services: list[str] | None = Field(
        default=None,
        description="Новый allowed_services (если задан — заменяет полностью).",
    )


class BotResponse(BaseModel):
    """Бот в ответе list/get эндпоинтов."""
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
    """Тело `POST /bots/{bot_id}/tokens`."""
    name: str = Field(min_length=1, max_length=256, description="Имя токена (для метаданных, не secret).")
    expires_at: datetime | None = Field(default=None, description="TTL токена. None = бессрочно.")


class BotTokenCreateResponse(BaseModel):
    """Возвращается один раз при создании — содержит raw-значение токена."""
    token_id: str = Field(description="ID записи токена (для revoke).")
    token: str = Field(description="Raw-токен с префиксом `dbos_bot_…`. Сохрани сейчас — больше не покажем.")
    name: str
    expires_at: datetime | None


class BotTokenListItem(BaseModel):
    """Элемент списка `GET /bots/{bot_id}/tokens` — только метаданные."""
    token_id: str
    name: str
    token_prefix: str
    created_at: datetime
    expires_at: datetime | None
    last_used_at: datetime | None
    revoked_at: datetime | None

    model_config = {"from_attributes": True}


class BotRoleAssignRequest(BaseModel):
    """Тело `POST /bots/{bot_id}/roles` — replace-семантика для (bot, service)."""
    service_name: str
    roles: list[str]


class BotRoleResponse(BaseModel):
    """Service-роли бота для конкретного сервиса."""
    service_name: str
    roles: list[str]
