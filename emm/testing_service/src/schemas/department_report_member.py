"""Pydantic-схемы для /departments/{department_id}/report-members (§9.1 плана миграции)."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class DepartmentReportMemberCreate(BaseModel):
    """Тело POST .../report-members. `department_id` берётся из пути, не из тела."""

    display_name: str = Field(..., min_length=1, max_length=128, description="ФИО/отображаемое имя сотрудника.")
    bitbucket_username: str | None = Field(
        default=None, max_length=128, description="Логин в Bitbucket (`commit.author.name`).",
    )
    jira_author_name: str | None = Field(
        default=None, max_length=128, description="`displayName` в Jira (комментарии к задачам).",
    )
    jira_tempo_worker_key: str | None = Field(
        default=None, max_length=128, description="Ключ воркера в Tempo (`worklog.worker`, обычно JIRAUSER...).",
    )
    is_active: bool = Field(default=True, description="Учитывать ли сотрудника при следующей генерации отчёта.")


class DepartmentReportMemberUpdate(BaseModel):
    """Тело PATCH .../report-members/{member_id}. Все поля опциональны."""

    display_name: str | None = Field(default=None, min_length=1, max_length=128)
    bitbucket_username: str | None = Field(default=None, max_length=128)
    jira_author_name: str | None = Field(default=None, max_length=128)
    jira_tempo_worker_key: str | None = Field(default=None, max_length=128)
    is_active: bool | None = Field(default=None)


class DepartmentReportMemberResponse(BaseModel):
    """Карточка сотрудника отдела в ответе."""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(description="Department report member ID (prefix drm_).")
    department_id: str
    display_name: str
    bitbucket_username: str | None = Field(default=None)
    jira_author_name: str | None = Field(default=None)
    jira_tempo_worker_key: str | None = Field(default=None)
    is_active: bool
    created_at: datetime
    updated_at: datetime
    created_by: str | None = Field(default=None)
