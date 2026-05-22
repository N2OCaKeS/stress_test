"""Схемы для регистра платформенных сервисов."""

from datetime import datetime

from pydantic import BaseModel, Field


class ServiceCreate(BaseModel):
    """Тело `POST /services`."""
    service_name: str = Field(description="Машинно-читаемое имя сервиса (`server_service`, `loging_service`, ...).")
    display_name: str = Field(description="Человеческое название.")
    description: str | None = Field(default=None)


class ServiceResponse(BaseModel):
    """Сервис в ответе list/get эндпоинтов."""
    service_name: str
    display_name: str
    description: str | None
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}
