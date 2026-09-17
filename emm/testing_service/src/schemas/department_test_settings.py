"""Pydantic-схемы для /department-test-settings.

Read — GET по department_id, всегда отдаёт что-то (дефолты, если строки нет).
Write — PUT, upsert; `department_id` берётся из пути, не из тела.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class DepartmentTestSettingsUpdate(BaseModel):
    """Тело PUT /department-test-settings/{department_id}. Upsert — все поля опциональны,
    заданные заменяют текущее значение (или дефолт, если строки ещё не было)."""

    retry_enabled: bool | None = Field(default=None, description="Ретраить ли провалившийся прогон один раз.")
    test_username: str | None = Field(
        default=None, min_length=1, max_length=32,
        description="Имя пользователя исполнения теста на стенде (передаётся в prepare-for-test).",
    )
    activity_report_auto_generate: bool | None = Field(
        default=None,
        description=(
            "Включить фоновую ежемесячную генерацию HR-отчёта за предыдущий месяц "
            "(1 числа каждого месяца)."
        ),
    )


class DepartmentTestSettingsResponse(BaseModel):
    """Карточка настроек отдела. Если строки в БД нет — отдаётся с дефолтами и `id=None`."""

    model_config = ConfigDict(from_attributes=True)

    id: str | None = Field(default=None, description="None, если строка ещё не создана (дефолты).")
    department_id: str = Field(description="Отдел, к которому относятся настройки.")
    retry_enabled: bool = Field(description="Ретраить ли провалившийся прогон один раз.")
    test_username: str = Field(description="Имя пользователя исполнения теста на стенде.")
    activity_report_auto_generate: bool = Field(
        description="Фоновая ежемесячная генерация HR-отчёта за предыдущий месяц включена.",
    )
    created_at: datetime | None = Field(default=None)
    updated_at: datetime | None = Field(default=None)
