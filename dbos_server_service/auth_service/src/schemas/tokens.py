"""Схемы для Personal Access Tokens (PAT)."""

from datetime import datetime

from pydantic import BaseModel, Field


class PATCreate(BaseModel):
    """Тело `POST /tokens`."""
    name: str = Field(min_length=1, max_length=256, description="Имя токена (для метаданных, не secret).")
    expires_at: datetime | None = Field(default=None, description="TTL токена. None = бессрочно.")
    allowed_services: list[str] = Field(
        default_factory=list,
        description="Scope токена — список сервисов, куда им можно ходить.",
    )


class PATCreateResponse(BaseModel):
    """Возвращается один раз при создании — содержит raw-значение токена."""
    token_id: str = Field(description="ID записи токена (для revoke).")
    token: str = Field(description="Raw-токен с префиксом `dbos_pat_…`. Сохрани сейчас — больше не покажем.")
    name: str
    expires_at: datetime | None


class PATListItem(BaseModel):
    """Элемент списка `GET /tokens` — только метаданные."""
    token_id: str
    name: str
    token_prefix: str
    allowed_services: list[str]
    created_at: datetime
    expires_at: datetime | None
    last_used_at: datetime | None
    revoked_at: datetime | None

    model_config = {"from_attributes": True}
