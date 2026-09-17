"""Pydantic-схемы для read-only зеркала доски спринта Jira (`GET .../sprint-board`)."""

from pydantic import BaseModel, Field


class JiraSprintBoardIssue(BaseModel):
    """Минимальная проекция issue Jira, достаточная для карточки на доске."""

    key: str
    summary: str
    status: str
    status_category: str | None = Field(
        default=None, description="statusCategory.key Jira: new / indeterminate / done.",
    )
    assignee: str | None = Field(default=None, description="displayName исполнителя, либо не назначен.")
    issue_type: str | None = Field(default=None, description="issuetype.name (Task/Bug/Story и т.д.).")


class JiraSprintBoardColumn(BaseModel):
    """Одна колонка доски — все issue с одинаковым статусом (`fields.status.name`)."""

    status: str
    issues: list[JiraSprintBoardIssue]


class JiraSprintInfo(BaseModel):
    id: int
    name: str
    start_date: str | None = Field(default=None)
    end_date: str | None = Field(default=None)


class JiraSprintBoardResponse(BaseModel):
    """Ответ `GET .../sprint-board`. Best-effort — отсутствие данных не 500, а `warning`."""

    configured: bool = Field(description="False, если у отдела не заведены jira_base_url/jira_board_id.")
    sprint: JiraSprintInfo | None = Field(default=None, description="None, если нет активного спринта.")
    columns: list[JiraSprintBoardColumn] = Field(default_factory=list)
    warning: str | None = Field(
        default=None,
        description="Человекочитаемая причина пустого результата: не настроено, нет активного спринта, Jira недоступна.",
    )
