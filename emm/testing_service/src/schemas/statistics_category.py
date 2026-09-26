"""Pydantic-схемы справочника `/statistics/categories` (D18).

Поля — ровно то, что уходит во внешний сервис статистики одним POST'ом
(`services/statistics_client.trigger_category_statistics`): маршрут `path` и
тело `{title_statistics, set_of_test_types, comparison_list?,
comparison_kernel_list?}`. `key` — идентификатор для `POST
/statistics/recalculate`, после создания не меняется.
"""

from __future__ import annotations

import re
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

_KEY_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def _strip_items(values: list[str]) -> list[str]:
    cleaned = [value.strip() for value in values]
    if any(not value for value in cleaned):
        raise ValueError("list items must be non-empty strings")
    return cleaned


def _validate_path(value: str) -> str:
    stripped = value.strip()
    if not stripped.startswith("/") or any(ch.isspace() for ch in stripped):
        raise ValueError("path must start with '/' and contain no whitespace (e.g. '/base-statistics')")
    return stripped


def _validate_comparisons(value: list[list[str]] | None) -> list[list[str]] | None:
    if value is None:
        return None
    result = []
    for group in value:
        if len(group) < 2:
            raise ValueError("each comparison must list at least two test types")
        result.append(_strip_items(group))
    return result


class StatisticsCategoryResponse(BaseModel):
    """Одна строка справочника."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    key: str
    label: str
    path: str
    title_statistics: str
    set_of_test_types: list[str]
    comparison_list: list[list[str]] | None = None
    comparison_kernel_list: list[str] | None = None
    enabled: bool
    sort_order: int
    created_at: datetime | None = None
    updated_at: datetime | None = None
    created_by: str | None = None


class StatisticsCategoriesResponse(BaseModel):
    """Справочник в порядке `sort_order` (без «всё сразу» — это отсутствие категорий)."""

    items: list[StatisticsCategoryResponse]


class StatisticsCategoryCreate(BaseModel):
    """Тело `POST /statistics/categories`."""

    key: str = Field(..., min_length=1, max_length=32, description="Машинный ключ, `[a-z][a-z0-9_]*`. UNIQUE, неизменяем.")
    label: str = Field(..., min_length=1, max_length=128, description="Подпись в модалке.")
    path: str = Field(..., min_length=2, max_length=128, description="Маршрут внешнего сервиса статистики.")
    title_statistics: str = Field(..., min_length=1, max_length=128)
    set_of_test_types: list[str] = Field(..., min_length=1, max_length=200)
    comparison_list: list[list[str]] | None = Field(default=None, max_length=200)
    comparison_kernel_list: list[str] | None = Field(default=None, max_length=200)
    enabled: bool = True
    sort_order: int = Field(default=0, ge=0, le=100_000)

    @field_validator("key")
    @classmethod
    def _key(cls, value: str) -> str:
        stripped = value.strip()
        if not _KEY_RE.match(stripped):
            raise ValueError("key must be a lower-case slug: [a-z][a-z0-9_]* (e.g. 'docker')")
        return stripped

    @field_validator("label", "title_statistics")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped

    @field_validator("path")
    @classmethod
    def _path(cls, value: str) -> str:
        return _validate_path(value)

    @field_validator("set_of_test_types")
    @classmethod
    def _types(cls, value: list[str]) -> list[str]:
        return _strip_items(value)

    @field_validator("comparison_kernel_list")
    @classmethod
    def _kernel(cls, value: list[str] | None) -> list[str] | None:
        return None if value is None else _strip_items(value)

    @field_validator("comparison_list")
    @classmethod
    def _comparisons(cls, value: list[list[str]] | None) -> list[list[str]] | None:
        return _validate_comparisons(value)


class StatisticsCategoryUpdate(BaseModel):
    """Тело `PATCH /statistics/categories/{id}` — частичное обновление, `key` не меняется.

    Для `comparison_list`/`comparison_kernel_list` явный `null` очищает поле
    (ключ перестаёт уходить во внешний сервис); непереданное поле не трогается.
    """

    label: str | None = Field(default=None, min_length=1, max_length=128)
    path: str | None = Field(default=None, min_length=2, max_length=128)
    title_statistics: str | None = Field(default=None, min_length=1, max_length=128)
    set_of_test_types: list[str] | None = Field(default=None, min_length=1, max_length=200)
    comparison_list: list[list[str]] | None = Field(default=None, max_length=200)
    comparison_kernel_list: list[str] | None = Field(default=None, max_length=200)
    enabled: bool | None = None
    sort_order: int | None = Field(default=None, ge=0, le=100_000)

    @field_validator("label", "title_statistics")
    @classmethod
    def _not_blank(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped

    @field_validator("path")
    @classmethod
    def _path(cls, value: str | None) -> str | None:
        return None if value is None else _validate_path(value)

    @field_validator("set_of_test_types")
    @classmethod
    def _types(cls, value: list[str] | None) -> list[str] | None:
        return None if value is None else _strip_items(value)

    @field_validator("comparison_kernel_list")
    @classmethod
    def _kernel(cls, value: list[str] | None) -> list[str] | None:
        return None if value is None else _strip_items(value)

    @field_validator("comparison_list")
    @classmethod
    def _comparisons(cls, value: list[list[str]] | None) -> list[list[str]] | None:
        return _validate_comparisons(value)
