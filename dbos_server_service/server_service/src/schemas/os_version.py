"""Pydantic-схемы для эндпоинтов /os-versions.

OS-версии — глобальный каталог. Read для всех с view, CRUD — admin.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class OsVersionCreate(BaseModel):
    """Тело POST /os-versions. `name` уникален."""

    name: str = Field(
        ..., min_length=1, max_length=128,
        description="Каноническое имя версии (astra-1.7, ubuntu-22.04, ...). UNIQUE.",
    )
    description: str | None = Field(
        default=None, description="Произвольное описание для UI/каталога.",
    )


class OsVersionUpdate(BaseModel):
    """Тело PATCH /os-versions/{os_version_id}. Все поля опциональны."""

    name: str | None = Field(
        default=None, min_length=1, max_length=128, description="Сменить имя (UNIQUE).",
    )
    description: str | None = Field(default=None, description="Сменить описание.")


class OsVersionResponse(BaseModel):
    """Карточка OS-версии в ответе."""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(description="OS version ID (prefix osv_).")
    name: str = Field(description="Имя версии.")
    description: str | None = Field(default=None, description="Описание.")
    discovered_at: datetime = Field(description="Когда версия добавлена в каталог.")
    updated_at: datetime = Field(description="Когда последний раз изменена.")
