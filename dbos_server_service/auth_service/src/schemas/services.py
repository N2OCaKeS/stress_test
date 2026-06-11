"""Схемы для регистра платформенных сервисов."""

from datetime import datetime

from pydantic import BaseModel, Field

# Имя сервиса используется как сегмент в "<service>.<resource>" (например
# `i.split(".", 1)[0]` в authorization_service / auth_service для PAT-scopes).
# Точка в имени ломает split, заглавные буквы — конвенция нарушают (snake_case),
# поэтому жёстко режем: lower-snake_case, начинается с буквы.
_SERVICE_NAME_PATTERN = r"^[a-z][a-z0-9_]+$"


class ServiceCreate(BaseModel):
    """Тело `POST /services`."""
    service_name: str = Field(
        description="Имя сервиса (`server_service`, `loging_service`, ...).",
        pattern=_SERVICE_NAME_PATTERN,
        min_length=2,
        max_length=64,
    )
    description: str | None = Field(default=None)


class ServiceResponse(BaseModel):
    """Сервис в ответе list/get эндпоинтов."""
    service_name: str
    description: str | None
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}
