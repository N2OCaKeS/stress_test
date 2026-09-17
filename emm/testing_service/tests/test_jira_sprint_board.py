"""Тесты read-only зеркала доски активного спринта Jira (`GET .../sprint-board`)."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from src.core.exceptions import NotFoundError, ServiceUnavailableError
from src.db.session import AsyncSessionLocal
from src.dependencies.auth import Identity
from src.repositories import department_integration_settings as dis_repo
from src.services import jira_report_client, jira_sprint_board as svc, secret_client
from src.utils.ids import department_integration_settings_id
from tests.conftest import auth_hdr as _hdr

BOARD_URL = "/api/testing/v1/department-integration-settings/{dept}/sprint-board"


def _identity(department_id: str = "dep_x") -> Identity:
    """Caller того же отдела — сервис гейтит доску по `department_id`."""
    return Identity(
        user_id="usr_sprint_board", username="tester", actor_type="user",
        department_id=department_id, allowed_services=["testing_service"],
        service_roles={"testing_service": ["admin"]}, is_banned=False, platform_role=None,
    )


@dataclass
class _Settings:
    credential_id: str | None = "cred_primary"
    jira_base_url: str | None = "http://jira.example"
    jira_board_id: str | None = "340"


async def _seed_settings(department_id: str, **overrides) -> None:
    async with AsyncSessionLocal() as db:
        data = {
            "id": department_integration_settings_id(),
            "department_id": department_id,
            "credential_id": "cred_primary",
            "jira_base_url": "http://jira.example",
            "jira_board_id": "340",
        }
        data.update(overrides)
        await dis_repo.create(db, data)
        await db.commit()


def _issue(key: str, status: str, category: str, assignee: str | None = None) -> dict:
    return {
        "key": key,
        "fields": {
            "summary": f"Summary for {key}",
            "status": {"name": status, "statusCategory": {"key": category}},
            "assignee": {"displayName": assignee} if assignee else None,
            "issuetype": {"name": "Task"},
        },
    }


class TestGetSprintBoardService:
    async def test_no_credential_is_not_configured(self, monkeypatch):
        settings = _Settings(credential_id=None)
        monkeypatch.setattr(dis_repo, "get_by_department", _fake_get_by_department(settings))
        result = await svc.get_sprint_board(None, _identity(), "dep_x")
        assert result.configured is False
        assert result.sprint is None
        assert "not configured" in result.warning

    async def test_no_active_sprint_is_clean_empty(self, monkeypatch):
        settings = _Settings()
        monkeypatch.setattr(dis_repo, "get_by_department", _fake_get_by_department(settings))

        async def fake_reveal(cred_id: str):
            return "bot", "jira_tok"

        async def fake_active_sprint(**kwargs):
            return None

        monkeypatch.setattr(secret_client, "reveal_credential", fake_reveal)
        monkeypatch.setattr(jira_report_client, "get_active_sprint", fake_active_sprint)

        result = await svc.get_sprint_board(None, _identity(), "dep_x")
        assert result.configured is True
        assert result.sprint is None
        assert result.columns == []
        assert "no active sprint" in result.warning

    async def test_jira_unreachable_is_handled_not_raised(self, monkeypatch):
        settings = _Settings()
        monkeypatch.setattr(dis_repo, "get_by_department", _fake_get_by_department(settings))

        async def fake_reveal(cred_id: str):
            return "bot", "jira_tok"

        async def fake_active_sprint(**kwargs):
            raise ServiceUnavailableError(error_code="JIRA_UNREACHABLE", message="boom")

        monkeypatch.setattr(secret_client, "reveal_credential", fake_reveal)
        monkeypatch.setattr(jira_report_client, "get_active_sprint", fake_active_sprint)

        result = await svc.get_sprint_board(None, _identity(), "dep_x")
        assert result.configured is True
        assert result.sprint is None
        assert "boom" in result.warning

    async def test_credential_reveal_failure_is_handled(self, monkeypatch):
        settings = _Settings()
        monkeypatch.setattr(dis_repo, "get_by_department", _fake_get_by_department(settings))

        async def fake_reveal(cred_id: str):
            raise NotFoundError(error_code="CREDENTIAL_NOT_FOUND", message="no credential")

        monkeypatch.setattr(secret_client, "reveal_credential", fake_reveal)

        result = await svc.get_sprint_board(None, _identity(), "dep_x")
        assert result.configured is True
        assert "credential unavailable" in result.warning

    async def test_issues_grouped_by_status(self, monkeypatch):
        settings = _Settings()
        monkeypatch.setattr(dis_repo, "get_by_department", _fake_get_by_department(settings))

        async def fake_reveal(cred_id: str):
            return "bot", "jira_tok"

        async def fake_active_sprint(**kwargs):
            return {"id": 42, "name": "Sprint 42", "startDate": "2026-09-01", "endDate": "2026-09-14"}

        async def fake_search_issues(**kwargs):
            return [
                _issue("QA-1", "In Progress", "indeterminate", assignee="Ivan Ivanov"),
                _issue("QA-2", "To Do", "new"),
                _issue("QA-3", "Done", "done"),
                _issue("QA-4", "In Progress", "indeterminate"),
            ]

        monkeypatch.setattr(secret_client, "reveal_credential", fake_reveal)
        monkeypatch.setattr(jira_report_client, "get_active_sprint", fake_active_sprint)
        monkeypatch.setattr(jira_report_client, "search_sprint_issues", fake_search_issues)

        result = await svc.get_sprint_board(None, _identity(), "dep_x")
        assert result.configured is True
        assert result.warning is None
        assert result.sprint.id == 42
        assert result.sprint.name == "Sprint 42"

        statuses = [column.status for column in result.columns]
        assert statuses == ["To Do", "In Progress", "Done"]

        in_progress = next(c for c in result.columns if c.status == "In Progress")
        assert [i.key for i in in_progress.issues] == ["QA-1", "QA-4"]
        assert in_progress.issues[0].assignee == "Ivan Ivanov"
        assert in_progress.issues[1].assignee is None


def _fake_get_by_department(settings):
    async def _inner(db, department_id):
        return settings

    return _inner


class TestSprintBoardEndpoint:
    async def test_anonymous_401(self, client):
        resp = await client.get(BOARD_URL.format(dept="dep_a"))
        assert resp.status_code == 401, resp.text

    async def test_not_configured_returns_clean_body(self, client, make_token):
        token = make_token(department_id="dep_noconf", service_roles={"testing_service": ["guest"]})
        resp = await client.get(BOARD_URL.format(dept="dep_noconf"), headers=_hdr(token))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["configured"] is False
        assert body["columns"] == []

    async def test_happy_path_returns_grouped_columns(self, client, make_token, monkeypatch):
        dept = "dep_board"
        await _seed_settings(dept)
        token = make_token(department_id=dept, service_roles={"testing_service": ["guest"]})

        async def fake_reveal(cred_id: str):
            return "bot", "jira_tok"

        async def fake_active_sprint(**kwargs):
            return {"id": 7, "name": "Sprint 7", "startDate": "2026-09-01", "endDate": "2026-09-14"}

        async def fake_search_issues(**kwargs):
            return [_issue("QA-9", "To Do", "new")]

        monkeypatch.setattr(secret_client, "reveal_credential", fake_reveal)
        monkeypatch.setattr(jira_report_client, "get_active_sprint", fake_active_sprint)
        monkeypatch.setattr(jira_report_client, "search_sprint_issues", fake_search_issues)

        resp = await client.get(BOARD_URL.format(dept=dept), headers=_hdr(token))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["configured"] is True
        assert body["sprint"]["name"] == "Sprint 7"
        assert body["columns"] == [
            {
                "status": "To Do",
                "issues": [
                    {
                        "key": "QA-9",
                        "summary": "Summary for QA-9",
                        "status": "To Do",
                        "status_category": "new",
                        "assignee": None,
                        "issue_type": "Task",
                    }
                ],
            }
        ]
