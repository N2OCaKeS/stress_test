"""Pydantic-схемы для эндпоинтов /console-macros.

Макрос консоли — сохранённая команда терминала. Личный (виден/правит только
автор) либо системный (общий в отделе, ведёт department_admin). Флаг
`is_system` в теле POST выбирает скоуп; для системного нужен department_admin.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ConsoleMacroCreate(BaseModel):
    """Тело POST /console-macros.

    `is_system=false` — личный макрос, привязывается к вызывающему. `is_system
    =true` — системный (общий в отделе), доступен только department_admin своего
    отдела; отдел берётся из identity вызывающего, в теле не задаётся.
    """

    name: str = Field(
        ..., min_length=1, max_length=128,
        description="Короткое имя макроса для списка в UI.",
    )
    command_text: str = Field(
        ..., min_length=1, max_length=8192,
        description="Команда, которая подставится в терминал.",
    )
    display_order: int = Field(
        default=0, ge=0,
        description="Порядок в списке UI (по возрастанию).",
    )
    is_system: bool = Field(
        default=False,
        description="true — системный (общий в отделе, только department_admin); false — личный.",
    )


class ConsoleMacroUpdate(BaseModel):
    """Тело PATCH /console-macros/{id}. Все поля опциональны.

    Скоуп макроса (личный/системный) не меняется — `is_system` сюда не
    выносится: смена скоупа = create нового + delete старого.
    """

    name: str | None = Field(
        default=None, min_length=1, max_length=128, description="Сменить имя.",
    )
    command_text: str | None = Field(
        default=None, min_length=1, max_length=8192, description="Сменить команду.",
    )
    display_order: int | None = Field(
        default=None, ge=0, description="Сменить порядок в списке.",
    )


class ConsoleMacroResponse(BaseModel):
    """Карточка макроса в ответе."""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(description="Macro ID (prefix cmc_).")
    name: str = Field(description="Имя макроса.")
    command_text: str = Field(description="Команда терминала.")
    display_order: int = Field(description="Порядок в списке UI.")
    is_system: bool = Field(description="true — системный, false — личный.")
    user_id: str | None = Field(default=None, description="Владелец личного макроса; null у системного.")
    department_id: str | None = Field(default=None, description="Отдел макроса.")
    created_by: str = Field(description="Кто создал.")
    created_at: datetime = Field(description="Когда создан.")
    updated_at: datetime = Field(description="Когда последний раз изменён.")
