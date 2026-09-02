"""Схемы для `ServiceRoleDefinition` (per-department scope)."""

from datetime import datetime

from pydantic import BaseModel, Field

# Жёсткая форма для role_name по той же причине, что и service_name:
# split('.', 1) над ключами и связками "<service>.<role>" ломается на точках,
# а заглавные буквы и спецсимволы расходятся с system-ролями (`admin`,
# `operator`, `reader`, `worker_bot`). lower-snake_case, начинается с буквы.
_ROLE_NAME_PATTERN = r"^[a-z][a-z0-9_]+$"


class ServiceRoleCreate(BaseModel):
    """Тело `POST /departments/{dept_id}/services/{service}/roles`."""
    role_name: str = Field(
        description="Имя роли (`admin`, `operator`, `reader`).",
        pattern=_ROLE_NAME_PATTERN,
        min_length=2,
        max_length=64,
    )
    description: str | None = Field(default=None)


class ServiceRoleUpdate(BaseModel):
    """Тело PATCH — меняется только description."""
    description: str | None = None


class ServiceRoleResponse(BaseModel):
    """ServiceRoleDefinition в ответе list/get эндпоинтов."""
    id: str
    department_id: str
    service_name: str
    role_name: str
    description: str | None
    is_active: bool
    is_system: bool = Field(description="True для системных ролей (`admin`) — защищены от удаления.")
    created_at: datetime
    created_by: str | None

    model_config = {"from_attributes": True}
