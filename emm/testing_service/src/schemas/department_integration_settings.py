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
    confluence_credential_id: str | None = Field(
        default=None, max_length=64,
        description=(
            "Id credential в secret_service для Confluence (может отличаться от Jira). "
            "Пусто — Confluence-публикации используют credential_id."
        ),
    )
    bitbucket_base_url: str | None = Field(
        default=None, max_length=256, description="Base URL Bitbucket Server этого отдела.",
    )
    bitbucket_project_key: str | None = Field(
        default=None, max_length=64, description="Project key репозитория с коммитами отдела.",
    )
    bitbucket_repo_slug: str | None = Field(
        default=None, max_length=128, description="Slug репозитория с коммитами отдела.",
    )
    bitbucket_credential_id: str | None = Field(
        default=None, max_length=64,
        description="Id credential в secret_service для Bitbucket basic auth (может отличаться от Jira/Confluence).",
    )
    jira_board_id: str | None = Field(
        default=None, max_length=32, description="Id доски Jira (Scrum board) для подсчёта спринтов отдела.",
    )
    tempo_team_id: str | None = Field(
        default=None, max_length=32, description="Id команды Tempo отдела.",
    )
    confluence_report_page_space: str | None = Field(
        default=None, max_length=64,
        description="Confluence space для публикации HR-отчёта. Пусто — отчёт для отдела недоступен.",
    )
    confluence_report_parent_page_title: str | None = Field(
        default=None, max_length=256,
        description="Заголовок родительской страницы, под которой заводится месячная страница отчёта.",
    )
    stp_matrix_confluence_space: str | None = Field(
        default=None, max_length=64,
        description="Confluence space для публикации сводной СТП-матрицы. Пусто — публикация недоступна.",
    )
    stp_matrix_confluence_root_page_title: str | None = Field(
        default=None, max_length=256,
        description="Заголовок корневой (grandparent) страницы иерархии СТП-матрицы.",
    )


class DepartmentIntegrationSettingsResponse(BaseModel):
    """Карточка настроек интеграции отдела. Если строки в БД нет — отдаётся с пустыми полями."""

    model_config = ConfigDict(from_attributes=True)

    id: str | None = Field(default=None, description="None, если строка ещё не создана.")
    department_id: str
    credential_id: str | None = Field(default=None)
    jira_base_url: str | None = Field(default=None)
    confluence_base_url: str | None = Field(default=None)
    confluence_credential_id: str | None = Field(default=None)
    bitbucket_base_url: str | None = Field(default=None)
    bitbucket_project_key: str | None = Field(default=None)
    bitbucket_repo_slug: str | None = Field(default=None)
    bitbucket_credential_id: str | None = Field(default=None)
    jira_board_id: str | None = Field(default=None)
    tempo_team_id: str | None = Field(default=None)
    confluence_report_page_space: str | None = Field(default=None)
    confluence_report_parent_page_title: str | None = Field(default=None)
    stp_matrix_confluence_space: str | None = Field(default=None)
    stp_matrix_confluence_root_page_title: str | None = Field(default=None)
    created_at: datetime | None = Field(default=None)
    updated_at: datetime | None = Field(default=None)
