"""Pydantic-схемы `/statistics/status` и `/statistics/recalculate` (§2.7, §9.3 плана миграции)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class StatisticsRecalcStatusResponse(BaseModel):
    """Индикатор фонового пересчёта — для левой панели UI."""

    status: str
    triggered_by: str | None = None
    category: str | None = None
    # весь набор семейств запуска (ключи справочника), NULL — полный пересчёт.
    categories: list[str] | None = None
    test_run_id: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None
    updated_at: datetime | None = None


class StatisticsRecalcTriggerRequest(BaseModel):
    """Тело ручного триггера. `department_id` не задан → берётся отдел вызывающего."""

    department_id: str | None = None
    # Ключ семейства тестов из `GET /statistics/categories`. Не задан — полный
    # пересчёт (`/all-statistics`), как было до возврата пер-категорийных кнопок.
    category: str | None = None
    # несколько семейств за один запуск (модалка пересчёта). Считаются
    # последовательно в порядке справочника одной фоновой задачей. Объединяется
    # с `category`; оба пустые — полный пересчёт.
    categories: list[str] | None = Field(default=None, max_length=100)
