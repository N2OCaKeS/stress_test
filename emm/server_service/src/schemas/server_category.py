"""Pydantic-схемы для эндпоинтов /server-categories.

Категории серверов по мощности — платформенный каталог. Read доступен любому
аутентифицированному актору, запись — под матрицей прав.
"""

import re
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Слаг категории: латиница в нижнем регистре, цифры и подчёркивания.
# Именно он уезжает в testing_service как машинный ключ роли стенда.
_CODE_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def _validate_code(value: str) -> str:
    """Нормализует и проверяет слаг: строчная латиница, начинается с буквы."""
    stripped = value.strip()
    if not _CODE_RE.match(stripped):
        raise ValueError(
            "code must be a lowercase slug: [a-z][a-z0-9_]* (e.g. 'low_server')"
        )
    return stripped


class ServerCategoryCreate(BaseModel):
    """Тело POST /server-categories. `code` уникален."""

    code: str = Field(
        ..., min_length=1, max_length=64,
        description="Машинный слаг категории (low_server, middle_server, ...). UNIQUE.",
    )
    label: str = Field(
        ..., min_length=1, max_length=128,
        description="Человекочитаемое имя категории для UI (LowServer, ...).",
    )
    description: str | None = Field(
        default=None, description="Произвольное пояснение, чем эта категория отличается.",
    )

    @field_validator("code")
    @classmethod
    def _check_code(cls, value: str) -> str:
        return _validate_code(value)


class ServerCategoryUpdate(BaseModel):
    """Тело PATCH /server-categories/{category_id}. Все поля опциональны."""

    code: str | None = Field(
        default=None, min_length=1, max_length=64, description="Сменить слаг (UNIQUE).",
    )
    label: str | None = Field(
        default=None, min_length=1, max_length=128, description="Сменить отображаемое имя.",
    )
    description: str | None = Field(default=None, description="Сменить пояснение.")

    @field_validator("code")
    @classmethod
    def _check_code(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_code(value)


class ServerCategoryResponse(BaseModel):
    """Карточка категории в ответе."""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(description="Server category ID (prefix scat_).")
    code: str = Field(description="Машинный слаг категории.")
    label: str = Field(description="Отображаемое имя.")
    description: str | None = Field(default=None, description="Пояснение.")
    created_at: datetime = Field(description="Когда категория заведена.")
    updated_at: datetime = Field(description="Когда последний раз изменена.")
    created_by: str | None = Field(
        default=None, description="Кто завёл категорию (у сидированных — пусто).",
    )
