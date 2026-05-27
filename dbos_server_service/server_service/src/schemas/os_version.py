"""Pydantic-схемы для эндпоинтов /os-versions.

OS-версии — глобальный каталог. Read публичный (без auth), CRUD — под матрицей прав.
"""

from datetime import datetime
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Каталог версий — это пара десятков apt/yum-репозиториев на запись, не больше.
_MAX_REPOSITORIES = 64
_MAX_REPOSITORY_URL_LEN = 2048


def _validate_repositories(value: list[str] | None) -> list[str] | None:
    """Каждый элемент — непустой http(s)-URL с хостом, в разумных лимитах."""
    if value is None:
        return value
    if len(value) > _MAX_REPOSITORIES:
        raise ValueError(f"too many repositories (max {_MAX_REPOSITORIES})")
    for item in value:
        if not isinstance(item, str):
            raise ValueError("repository must be a string")
        if len(item) > _MAX_REPOSITORY_URL_LEN:
            raise ValueError(f"repository URL too long (max {_MAX_REPOSITORY_URL_LEN})")
        parsed = urlparse(item.strip())
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError(f"repository must be a valid http(s) URL: {item!r}")
    return value


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

    @field_validator("repositories")
    @classmethod
    def _check_repositories(cls, value: list[str]) -> list[str]:
        return _validate_repositories(value)


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

    @field_validator("repositories")
    @classmethod
    def _check_repositories(cls, value: list[str] | None) -> list[str] | None:
        return _validate_repositories(value)


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
