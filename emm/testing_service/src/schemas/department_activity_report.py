"""Pydantic-схемы для /departments/{department_id}/activity-reports (§2.7, §9.1 плана миграции)."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class DepartmentActivityReportGenerateRequest(BaseModel):
    """Тело POST .../activity-reports/generate."""

    period: str = Field(
        ..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$",
        description="Период отчёта, 'YYYY-MM' (например '2026-09').",
    )


class DepartmentActivityReportResponse(BaseModel):
    """Одна попытка генерации отчёта в списке/после запуска."""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(description="Department activity report ID (prefix dar_).")
    department_id: str
    period: str
    generated_at: datetime
    generated_by: str | None = Field(default=None)
    confluence_page_id: str | None = Field(default=None)
    status: str = Field(description="'generating' / 'done' / 'failed'.")
    error: str | None = Field(default=None, description="Короткое сообщение при status='failed'.")
