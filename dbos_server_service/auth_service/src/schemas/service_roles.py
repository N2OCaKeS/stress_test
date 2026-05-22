"""Схемы для `ServiceRoleDefinition` (per-department scope)."""

from datetime import datetime

from pydantic import BaseModel, Field


class ServiceRoleCreate(BaseModel):
    """Тело `POST /departments/{dept_id}/services/{service}/roles`."""
    role_name: str = Field(description="Машинно-читаемое имя роли (`admin`, `operator`, `reader`).")
    display_name: str = Field(description="Человеческое название.")
    description: str | None = Field(default=None)


class ServiceRoleUpdate(BaseModel):
    """Тело PATCH — меняются только display_name и description."""
    display_name: str | None = None
    description: str | None = None


class ServiceRoleResponse(BaseModel):
    """ServiceRoleDefinition в ответе list/get эндпоинтов."""
    id: str
    department_id: str
    service_name: str
    role_name: str
    display_name: str
    description: str | None
    is_active: bool
    is_system: bool = Field(description="True для системных ролей (`admin`) — защищены от удаления.")
    created_at: datetime
    created_by: str | None

    model_config = {"from_attributes": True}
