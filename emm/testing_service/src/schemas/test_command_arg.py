"""Pydantic-схемы для эндпоинтов /test-definitions/{test_id}/args.

Взаимоисключение `literal_value`/`variable_id` по `kind` проверяется в
`services/test_command_arg.py`, не здесь — схема сама по себе не знает, что
такое существующая переменная, это уже бизнес-правило со своим кодом ошибки.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from src.core.constants import CommandArgKind


class TestCommandArgCreate(BaseModel):
    """Тело POST /test-definitions/{test_id}/args — добавить слот в конец команды."""

    position: int | None = Field(
        default=None, ge=0,
        description="Порядок слота. Не задан — слот добавляется в конец списка.",
    )
    kind: CommandArgKind = Field(description="literal — фиксированное значение, variable — ссылка на переменную.")
    literal_value: str | None = Field(
        default=None, description="Значение слота. Обязательно при kind=literal.",
    )
    variable_id: str | None = Field(
        default=None, description="Ссылка на global_variables.id. Обязательно при kind=variable.",
    )
    override_value: str | None = Field(
        default=None,
        description="Per-test переопределение значения переменной (только для kind=variable).",
    )


class TestCommandArgUpdate(BaseModel):
    """Тело PATCH /test-definitions/{test_id}/args/{arg_id}. Все поля опциональны."""

    position: int | None = Field(default=None, ge=0, description="Переставить слот на новую позицию.")
    kind: CommandArgKind | None = Field(default=None, description="Сменить тип слота.")
    literal_value: str | None = Field(default=None, description="Сменить литеральное значение.")
    variable_id: str | None = Field(default=None, description="Сменить ссылку на переменную.")
    override_value: str | None = Field(default=None, description="Сменить per-test override.")


class TestCommandArgResponse(BaseModel):
    """Один слот команды в ответе."""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(description="Test command arg ID (prefix targ_).")
    test_id: str = Field(description="Тест, которому принадлежит слот.")
    position: int = Field(description="Порядок слота в команде.")
    kind: str = Field(description="literal / variable.")
    literal_value: str | None = Field(default=None, description="Значение при kind=literal.")
    variable_id: str | None = Field(default=None, description="Ссылка на переменную при kind=variable.")
    override_value: str | None = Field(default=None, description="Per-test override значения переменной.")
    created_at: datetime = Field(description="Когда слот добавлен.")
    updated_at: datetime = Field(description="Когда последний раз изменён.")
