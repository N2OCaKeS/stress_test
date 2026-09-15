"""Pydantic-схемы `/statistics/status` и `/statistics/recalculate` (§2.7, §9.3 плана миграции)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class StatisticsRecalcStatusResponse(BaseModel):
    """Индикатор фонового пересчёта — для левой панели UI."""

    status: str
    triggered_by: str | None = None
    test_run_id: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None
    updated_at: datetime | None = None


class StatisticsRecalcTriggerRequest(BaseModel):
    """Тело ручного триггера. `department_id` не задан → берётся отдел вызывающего."""

    department_id: str | None = None
