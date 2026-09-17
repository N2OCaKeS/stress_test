"""Read-only зеркало доски активного спринта Jira отдела (`GET .../sprint-board`).

Чисто на чтение — write-операций в Jira здесь нет и не планируется. Устройство
зеркалит `department_integration_settings.get_effective`: чтение — своему
отделу, отсутствие настройки или недоступность
Jira — не исключение, а `warning` в самом ответе (best-effort: страница
владельца отдела не должна падать 500 из-за того, что Jira недоступна или
интеграция ещё не заведена).

Группировка по `fields.status.name` (реальное имя статуса воркфлоу), а не по
`statusCategory` — задача была "дубль доски спринта из Jira", а у доски
обычно больше трёх колонок (например, "Код-ревью" между "In Progress" и
"Done"); категория используется только для сортировки колонок в привычном
порядке To Do → In Progress → Done.

**Не проверено против живой Jira** — как и `jira_report_client.py`, реализация
построена по документированному контракту Jira Agile REST API, не по
работающему запросу к реальному инстансу.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import AppException
from src.dependencies.auth import Identity
from src.repositories import department_integration_settings as dis_repo
from src.schemas.jira_sprint_board import (
    JiraSprintBoardColumn,
    JiraSprintBoardIssue,
    JiraSprintBoardResponse,
    JiraSprintInfo,
)
from src.services import jira_report_client, permissions, secret_client

_CATEGORY_ORDER = {"new": 0, "indeterminate": 1, "done": 2}


def _extract_issue(raw: dict) -> JiraSprintBoardIssue:
    fields = raw.get("fields") or {}
    status = fields.get("status") or {}
    assignee = fields.get("assignee") or {}
    issuetype = fields.get("issuetype") or {}
    return JiraSprintBoardIssue(
        key=raw.get("key") or "",
        summary=fields.get("summary") or "",
        status=status.get("name") or "Unknown",
        status_category=(status.get("statusCategory") or {}).get("key"),
        assignee=assignee.get("displayName"),
        issue_type=issuetype.get("name"),
    )


def _group_by_status(issues: list[dict]) -> list[JiraSprintBoardColumn]:
    grouped: dict[str, list[JiraSprintBoardIssue]] = {}
    category_by_status: dict[str, str | None] = {}
    first_seen: dict[str, int] = {}
    for position, raw in enumerate(issues):
        issue = _extract_issue(raw)
        if issue.status not in grouped:
            grouped[issue.status] = []
            category_by_status[issue.status] = issue.status_category
            first_seen[issue.status] = position
        grouped[issue.status].append(issue)
    ordered_statuses = sorted(
        grouped, key=lambda name: (_CATEGORY_ORDER.get(category_by_status[name], 1), first_seen[name]),
    )
    return [JiraSprintBoardColumn(status=name, issues=grouped[name]) for name in ordered_statuses]


def _sprint_info(sprint: dict) -> JiraSprintInfo:
    return JiraSprintInfo(
        id=sprint["id"],
        name=sprint.get("name") or "",
        start_date=sprint.get("startDate"),
        end_date=sprint.get("endDate"),
    )


async def get_sprint_board(
    db: AsyncSession, identity: Identity, department_id: str,
) -> JiraSprintBoardResponse:
    """Собирает доску активного спринта отдела.

    Best-effort только про внешнюю Jira: провал похода наружу уезжает в
    `warning`, а не в исключение. Гейт по отделу — наоборот, жёсткий: вызов
    раскрывает кредентиалу отдела на стороне сервиса и ходит ей в Jira.
    """
    permissions.require_own_department(identity, department_id)
    settings = await dis_repo.get_by_department(db, department_id)
    if settings is None or not settings.jira_base_url or not settings.jira_board_id or not settings.credential_id:
        return JiraSprintBoardResponse(
            configured=False,
            warning="Jira integration is not configured for this department "
            "(jira_base_url/jira_board_id/credential_id missing)",
        )

    try:
        _login, jira_token = await secret_client.reveal_credential(settings.credential_id)
    except AppException as exc:
        return JiraSprintBoardResponse(configured=True, warning=f"jira credential unavailable: {exc.message}")
    if not jira_token:
        return JiraSprintBoardResponse(configured=True, warning="jira credential unavailable: empty secret")

    try:
        sprint = await jira_report_client.get_active_sprint(
            base_url=settings.jira_base_url, jira_token=jira_token, board_id=settings.jira_board_id,
        )
    except AppException as exc:
        return JiraSprintBoardResponse(configured=True, warning=f"jira: {exc.message}")

    if sprint is None:
        return JiraSprintBoardResponse(configured=True, warning="no active sprint on this board")

    try:
        raw_issues = await jira_report_client.search_sprint_issues(
            base_url=settings.jira_base_url, jira_token=jira_token, sprint_id=sprint["id"],
        )
    except AppException as exc:
        return JiraSprintBoardResponse(configured=True, sprint=_sprint_info(sprint), warning=f"jira: {exc.message}")

    return JiraSprintBoardResponse(
        configured=True, sprint=_sprint_info(sprint), columns=_group_by_status(raw_issues),
    )
