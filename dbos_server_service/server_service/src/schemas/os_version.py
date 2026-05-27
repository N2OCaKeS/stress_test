"""Pydantic-схемы для эндпоинтов /os-versions.

OS-версии — глобальный каталог. Read публичный (без auth), CRUD — под матрицей прав.
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
    repositories: list[str] = Field(
        default_factory=list,
        description="URL-адреса репозиториев версии (apt/yum/...).",
    )


class OsVersionUpdate(BaseModel):
    """Тело PATCH /os-versions/{os_version_id}. Все поля опциональны."""

    name: str | None = Field(
        default=None, min_length=1, max_length=128, description="Сменить имя (UNIQUE).",
    )
    description: str | None = Field(default=None, description="Сменить описание.")
    repositories: list[str] | None = Field(
        default=None,
        description="Заменить список репозиториев целиком.",
    )


class OsVersionResponse(BaseModel):
    """Карточка OS-версии в ответе."""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(description="OS version ID (prefix osv_).")
    name: str = Field(description="Имя версии.")
    description: str | None = Field(default=None, description="Описание.")
    repositories: list[str] = Field(
        default_factory=list, description="URL-адреса репозиториев версии.",
    )
    discovered_at: datetime = Field(description="Когда версия добавлена в каталог.")
    updated_at: datetime = Field(description="Когда последний раз изменена.")
