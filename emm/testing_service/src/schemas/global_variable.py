"""Pydantic-схемы для эндпоинтов /global-variables.

Каталог платформенный: переменные принадлежат всей платформе, а не отделу.
Чтение доступно любому аутентифицированному актору, запись — под матрицей
прав `(global_variable, *, create|update|delete)`.
"""

import re
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.core.constants import GlobalVariableSource, GlobalVariableValueType

# Код переменной — то, чем на неё ссылается слот команды: заглавная латиница,
# цифры и подчёркивания (RC, TEST_SSH_KEY). Формат совпадает с окружением,
# в котором эти же имена жили в легаси-скриптах.
_CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")

# Строка `choices_source` целиком: "static:<json>" может быть длинной, но не
# бесконечной — фиксированные множества это единицы значений, не тысячи.
MAX_CHOICES_SOURCE_LEN = 512


def _validate_code(value: str) -> str:
    """Нормализует и проверяет код: заглавная латиница, начинается с буквы."""
    stripped = value.strip()
    if not _CODE_RE.match(stripped):
        raise ValueError(
            "code must be an upper-case slug: [A-Z][A-Z0-9_]* (e.g. 'TEST_USER')"
        )
    return stripped


class GlobalVariableCreate(BaseModel):
    """Тело POST /global-variables. `code` уникален."""

    code: str = Field(
        ..., min_length=1, max_length=64,
        description="Машинный код переменной (RC, STAND, TEST_USER, ...). UNIQUE.",
    )
    label: str = Field(
        ..., min_length=1, max_length=128,
        description="Человекочитаемое имя для UI конструктора команд.",
    )
    source: GlobalVariableSource = Field(
        description=(
            "Откуда берётся значение: launch_context (снэпшот запуска), "
            "static (фиксированное), per_test_override (задаётся слотом теста), "
            "secret_service (живой reveal по credential_id)."
        ),
    )
    value_type: GlobalVariableValueType = Field(
        default=GlobalVariableValueType.STRING,
        description="Тип значения: string / integer / boolean.",
    )
    choices_source: str | None = Field(
        default=None, max_length=MAX_CHOICES_SOURCE_LEN,
        description=(
            "Способ получить список значений, а не сам список: "
            "`static:<json>` либо `dynamic:<resolver>` "
            "(`dynamic:os_versions`, `dynamic:kernels`). Пусто — свободный ввод."
        ),
    )
    is_sensitive: bool = Field(
        default=False,
        description="Значение маскируется в логах прогона как `***`.",
    )
    description: str | None = Field(
        default=None, description="Пояснение, зачем переменная и что в ней лежит.",
    )

    @field_validator("code")
    @classmethod
    def _check_code(cls, value: str) -> str:
        return _validate_code(value)


class GlobalVariableUpdate(BaseModel):
    """Тело PATCH /global-variables/{variable_id}. Все поля опциональны."""

    code: str | None = Field(
        default=None, min_length=1, max_length=64, description="Сменить код (UNIQUE).",
    )
    label: str | None = Field(
        default=None, min_length=1, max_length=128, description="Сменить отображаемое имя.",
    )
    source: GlobalVariableSource | None = Field(default=None, description="Сменить источник значения.")
    value_type: GlobalVariableValueType | None = Field(default=None, description="Сменить тип значения.")
    choices_source: str | None = Field(
        default=None, max_length=MAX_CHOICES_SOURCE_LEN,
        description="Сменить способ получения списка значений. `null` — убрать список.",
    )
    is_sensitive: bool | None = Field(default=None, description="Включить/снять маскирование в логах.")
    description: str | None = Field(default=None, description="Сменить пояснение.")

    @field_validator("code")
    @classmethod
    def _check_code(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_code(value)


class GlobalVariableResponse(BaseModel):
    """Карточка переменной в ответе.

    Значения переменной в каталоге нет ни для одной `source` — маскировать
    в ответе нечего; `is_sensitive` едет наружу как метаданные, по которым
    воркер и логи прячут уже зарезолвленный аргумент.
    """

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(description="Global variable ID (prefix gvar_).")
    code: str = Field(description="Машинный код переменной.")
    label: str = Field(description="Отображаемое имя.")
    source: str = Field(description="Источник значения.")
    value_type: str = Field(description="Тип значения.")
    choices_source: str | None = Field(default=None, description="Способ получить список значений.")
    is_sensitive: bool = Field(description="Маскировать значение в логах.")
    description: str | None = Field(default=None, description="Пояснение.")
    created_at: datetime = Field(description="Когда переменная заведена.")
    updated_at: datetime = Field(description="Когда последний раз изменена.")
    created_by: str | None = Field(default=None, description="Кто завёл (у сидированных — пусто).")


class ChoiceItem(BaseModel):
    """Один вариант значения переменной."""

    value: str = Field(description="Значение, которое уедет в аргумент команды.")
    label: str = Field(description="Как вариант показывается в UI.")


class ChoicesResponse(BaseModel):
    """Ответ `GET /global-variables/{id}/choices`."""

    items: list[ChoiceItem] = Field(description="Варианты значений на момент запроса.")
    choices_source: str = Field(description="Строка-источник, из которой собран список.")
