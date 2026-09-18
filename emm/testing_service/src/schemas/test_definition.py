"""Pydantic-схемы для эндпоинтов /test-definitions.

Каталог тестов — per-department бизнес-данные (§2.2 плана миграции), в
отличие от платформенных `global_variables`. Чтение доступно любому
аутентифицированному актору, запись — под матрицей прав
`(test_definition, *, create|update|delete)`.
"""

import re
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator
from src.core.constants import TestMode, TestReadiness

# Код теста: латиница/цифры, `_`/`-`/`.` как разделители, не начинается с
# разделителя. Формат достаточно свободный, чтобы вместить легаси-имена вида
# `postgresql.balance` или `kernel_fill`, но защищает от пустых/пробельных строк.
_CODE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]*$")


def _validate_code(value: str) -> str:
    stripped = value.strip()
    if not _CODE_RE.match(stripped):
        raise ValueError(
            "code must start with an alphanumeric and contain only letters, "
            "digits, '_', '-' or '.'"
        )
    return stripped


class TestDefinitionCreate(BaseModel):
    """Тело POST /test-definitions. `code` уникален."""

    code: str = Field(
        ..., min_length=1, max_length=64,
        description="Машинный код теста (например, postgresql.balance). UNIQUE.",
    )
    full_name: str = Field(
        ..., min_length=1, max_length=256,
        description="Человекочитаемое название теста.",
    )
    category: str | None = Field(default=None, max_length=64, description="Категория теста.")
    matrix_label: str | None = Field(
        default=None, max_length=64,
        description=(
            "Короткая подпись строки в СТП-матрице (\"FS_EXT4\"). Пусто — "
            "матрица печатает полное название."
        ),
    )
    owner: str | None = Field(default=None, max_length=128, description="Ответственный за тест.")
    readiness: TestReadiness = Field(
        default=TestReadiness.DEVELOPMENT,
        description="ready — Рабочий; review — На проверке; broken — Неисправен; development — В разработке. Только ready допускает обычный запуск.",
    )
    mode: TestMode = Field(
        default=TestMode.OREL,
        description=(
            "Режим безопасности Astra, под которым тест исполняется — фиксируется "
            "здесь, не выбирается при запуске/в кампании. server_worker переключает "
            "на него стенд перед прогоном."
        ),
    )
    department_id: str | None = Field(
        default=None, description="Отдел-владелец теста. Пусто — платформенный тест.",
    )
    pinned_stand_id: str | None = Field(
        default=None,
        description=(
            "Стенд, к которому привязан тест. Ссылка без FK — стенды "
            "появятся отдельным доменом; снимается debug-режимом при запуске."
        ),
    )
    changelog_component: str | None = Field(
        default=None, max_length=128,
        description=(
            "Компонент ОС, используемый ТОЛЬКО changelog-фильтром СТП-генерации "
            "(§7). Пусто — тест в changelog-объём не попадает (как в легаси); "
            "в полный набор попадает по-прежнему."
        ),
    )
    starter_suffix: str | None = Field(
        default=None, max_length=16,
        description=(
            "Позиционный $5 у legacy starter.sh (kernel/balance/oom). Пусто — "
            "generic `run.py -n <dates_filename>` без дополнительного флага."
        ),
    )
    timeout_seconds: int | None = Field(
        default=None, gt=0,
        description="Свой SSH-таймаут исполнения (сек). Пусто — дефолт testing_worker'а.",
    )

    @field_validator("code")
    @classmethod
    def _check_code(cls, value: str) -> str:
        return _validate_code(value)


class TestDefinitionUpdate(BaseModel):
    """Тело PATCH /test-definitions/{test_id}. Все поля опциональны."""

    code: str | None = Field(default=None, min_length=1, max_length=64, description="Сменить код (UNIQUE).")
    full_name: str | None = Field(default=None, min_length=1, max_length=256, description="Сменить название.")
    category: str | None = Field(default=None, max_length=64, description="Сменить категорию.")
    matrix_label: str | None = Field(
        default=None, max_length=64, description="Сменить короткую подпись строки в СТП-матрице.",
    )
    owner: str | None = Field(default=None, max_length=128, description="Сменить ответственного.")
    readiness: TestReadiness | None = Field(default=None, description="Сменить статус теста вручную.")

    @field_validator("readiness")
    @classmethod
    def _check_readiness(cls, value: TestReadiness | None) -> TestReadiness:
        if value is None:
            raise ValueError("readiness cannot be null")
        return value
    mode: TestMode | None = Field(default=None, description="Сменить режим безопасности теста.")

    @field_validator("mode")
    @classmethod
    def _check_mode(cls, value: TestMode | None) -> TestMode:
        if value is None:
            raise ValueError("mode cannot be null")
        return value
    department_id: str | None = Field(default=None, description="Сменить отдел-владелец.")
    pinned_stand_id: str | None = Field(default=None, description="Сменить привязанный стенд.")
    changelog_component: str | None = Field(
        default=None, max_length=128, description="Сменить компонент changelog-фильтра.",
    )
    starter_suffix: str | None = Field(
        default=None, max_length=16, description="Сменить позиционный $5 у legacy starter.sh.",
    )
    timeout_seconds: int | None = Field(
        default=None, gt=0, description="Сменить свой SSH-таймаут исполнения (сек).",
    )

    @field_validator("code")
    @classmethod
    def _check_code(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_code(value)


class TestDefinitionResponse(BaseModel):
    """Карточка теста в ответе."""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(description="Test definition ID (prefix tdef_).")
    code: str = Field(description="Машинный код теста.")
    full_name: str = Field(description="Отображаемое название.")
    category: str | None = Field(default=None, description="Категория теста.")
    matrix_label: str | None = Field(default=None, description="Короткая подпись строки в СТП-матрице.")
    owner: str | None = Field(default=None, description="Ответственный за тест.")
    readiness: TestReadiness = Field(description="Ручной статус теста; не зависит от исхода запуска.")
    mode: TestMode = Field(description="Режим безопасности Astra, под которым тест исполняется.")
    department_id: str | None = Field(default=None, description="Отдел-владелец.")
    pinned_stand_id: str | None = Field(default=None, description="Привязанный стенд.")
    changelog_component: str | None = Field(default=None, description="Компонент changelog-фильтра СТП.")
    starter_suffix: str | None = Field(default=None, description="Позиционный $5 у legacy starter.sh.")
    timeout_seconds: int | None = Field(default=None, description="Свой SSH-таймаут исполнения (сек); пусто — дефолт testing_worker'а.")
    created_at: datetime = Field(description="Когда тест заведён.")
    updated_at: datetime = Field(description="Когда последний раз изменён.")
    created_by: str | None = Field(default=None, description="Кто завёл.")
