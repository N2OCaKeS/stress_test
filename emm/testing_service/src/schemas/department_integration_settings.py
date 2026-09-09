"""Pydantic-схемы для /department-integration-settings (§2.4, §3.5 плана миграции)."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class DepartmentIntegrationSettingsUpdate(BaseModel):
    """Тело PUT /department-integration-settings/{department_id}. Upsert — все поля опциональны."""

    credential_id: str | None = Field(
        default=None, max_length=64,
        description="Id credential в secret_service (Jira/Zephyr токен отдела).",
    )
    jira_base_url: str | None = Field(
        default=None, max_length=256, description="Base URL Jira этого отдела.",
    )
    confluence_base_url: str | None = Field(
        default=None, max_length=256, description="Base URL Confluence этого отдела.",
    )


class DepartmentIntegrationSettingsResponse(BaseModel):
    """Карточка настроек интеграции отдела. Если строки в БД нет — отдаётся с пустыми полями."""

    model_config = ConfigDict(from_attributes=True)

    id: str | None = Field(default=None, description="None, если строка ещё не создана.")
    department_id: str
    credential_id: str | None = Field(default=None)
    jira_base_url: str | None = Field(default=None)
    confluence_base_url: str | None = Field(default=None)
    created_at: datetime | None = Field(default=None)
    updated_at: datetime | None = Field(default=None)
