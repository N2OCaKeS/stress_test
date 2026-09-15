"""Тесты HR-отчёта по активности отдела (§2.7, §9.1 плана миграции).

`month_business_days`/`render_report_html` — чистые функции. `collect_activity`
сводит три мокнутых клиента (Bitbucket/Jira/Tempo) в `{member_id: {day: {...}}}`
без обращения к БД. `generate_report`/эндпоинты — end-to-end через ASGI-клиент
с мокнутыми `secret_client`/`confluence_client`/`bitbucket_client`/
`jira_report_client`/`tempo_client`, ни одного реального сетевого вызова.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone

import pytest

from src.core.exceptions import NotFoundError, ServiceUnavailableError
from src.db.session import AsyncSessionLocal
from src.repositories import department_integration_settings as dis_repo
from src.services import activity_report as svc
from src.services import bitbucket_client, confluence_client, jira_report_client, secret_client, tempo_client
from src.utils.ids import department_integration_settings_id
from tests.conftest import auth_hdr as _hdr

REPORTS_BASE = "/api/testing/v1/departments/{dept}/activity-reports"


@dataclass
class _Member:
    id: str
    bitbucket_username: str | None = None
    jira_author_name: str | None = None
    jira_tempo_worker_key: str | None = None


@dataclass
class _Settings:
    credential_id: str | None = "cred_primary"
    confluence_base_url: str | None = "http://confluence.example"
    confluence_report_page_space: str | None = "DEVQA"
    confluence_report_parent_page_title: str | None = "STRESS_report ⬝ Monthly report desk"
    jira_base_url: str | None = "http://jira.example"
    jira_board_id: str | None = "340"
    tempo_team_id: str | None = "7"
    bitbucket_base_url: str | None = "http://git.example"
    bitbucket_project_key: str | None = "QA"
    bitbucket_repo_slug: str | None = "stress_test"
    bitbucket_credential_id: str | None = "cred_bitbucket"


async def _seed_settings(department_id: str, **overrides) -> None:
    async with AsyncSessionLocal() as db:
        data = {
            "id": department_integration_settings_id(),
            "department_id": department_id,
            "credential_id": "cred_primary",
            "jira_base_url": "http://jira.example",
            "confluence_base_url": "http://confluence.example",
            "bitbucket_base_url": "http://git.example",
            "bitbucket_project_key": "QA",
            "bitbucket_repo_slug": "stress_test",
            "bitbucket_credential_id": "cred_bitbucket",
            "jira_board_id": "340",
            "tempo_team_id": "7",
            "confluence_report_page_space": "DEVQA",
            "confluence_report_parent_page_title": "STRESS_report ⬝ Monthly report desk",
        }
        data.update(overrides)
        await dis_repo.create(db, data)
        await db.commit()


def _commit_ms(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, 9, 0, tzinfo=timezone.utc).timestamp() * 1000)


# ── month_business_days ──────────────────────────────────────────────────────


class TestMonthBusinessDays:
    def test_excludes_weekends(self):
        days = svc.month_business_days("2026-09")
        assert all(d.weekday() < 5 for d in days)
        # September 2026: 5 и 6 сентября — суббота/воскресенье.
        assert date(2026, 9, 5) not in days
        assert date(2026, 9, 6) not in days
        assert date(2026, 9, 1) in days
        assert date(2026, 9, 30) in days


# ── render_report_html ───────────────────────────────────────────────────────


class TestRenderReportHtml:
    def test_idle_day_is_highlighted(self):
        day = date(2026, 9, 2)
        members = [_Member(id="m1"), _Member(id="m2")]
        metrics = {
            "m1": {day.isoformat(): {"bitbucket": 0, "jira": 0, "tempo_hours": 0.0, "tempo_tasks": []}},
            "m2": {day.isoformat(): {"bitbucket": 1, "jira": 0, "tempo_hours": 2.0, "tempo_tasks": ["QA-1"]}},
        }
        members[0].display_name = "Idle Member"
        members[1].display_name = "Active Member"
        html = svc.render_report_html(days=[day], members=members, metrics=metrics, jira_base_url="http://jira.example")
        assert "#ffffe1" in html
        assert "QA-1" in html
        assert 'href="http://jira.example/browse/QA-1"' in html

    def test_no_members_yields_placeholder(self):
        html = svc.render_report_html(days=[], members=[], metrics={}, jira_base_url=None)
        assert "нет активных сотрудников" in html


# ── collect_activity ──────────────────────────────────────────────────────────


class TestCollectActivity:
    async def test_full_success_aggregates_all_sources(self, monkeypatch):
        day = date(2026, 9, 2)
        member = _Member(id="m1", bitbucket_username="ivanov", jira_author_name="Ivan Ivanov", jira_tempo_worker_key="JIRAUSER100")

        async def fake_reveal(cred_id: str):
            assert cred_id == "cred_bitbucket"
            return "bb_login", "bb_pass"

        async def fake_branches(**kwargs):
            return ["master"]

        async def fake_commits(**kwargs):
            return [{"author": {"name": "ivanov"}, "authorTimestamp": _commit_ms(day)}]

        async def fake_sprint_ids(**kwargs):
            return [1]

        async def fake_sprint_details(**kwargs):
            return {"id": 1, "startDate": f"{day.isoformat()}T00:00:00"}

        async def fake_search_issues(**kwargs):
            return [{"key": "QA-1"}]

        async def fake_issue_comments(**kwargs):
            return [{"author": {"displayName": "Ivan Ivanov"}, "created": f"{day.isoformat()}T10:00:00.000+0300"}]

        async def fake_worklogs(**kwargs):
            return [{"worker": "JIRAUSER100", "started": f"{day.isoformat()} 08:00:00.000000", "timeSpentSeconds": 7200, "issue": {"key": "QA-2"}}]

        monkeypatch.setattr(secret_client, "reveal_credential", fake_reveal)
        monkeypatch.setattr(bitbucket_client, "get_branches", fake_branches)
        monkeypatch.setattr(bitbucket_client, "get_commits", fake_commits)
        monkeypatch.setattr(jira_report_client, "get_sprint_ids", fake_sprint_ids)
        monkeypatch.setattr(jira_report_client, "get_sprint_details", fake_sprint_details)
        monkeypatch.setattr(jira_report_client, "search_sprint_issues", fake_search_issues)
        monkeypatch.setattr(jira_report_client, "get_issue_comments", fake_issue_comments)
        monkeypatch.setattr(tempo_client, "search_worklogs", fake_worklogs)

        metrics, warnings = await svc.collect_activity(_Settings(), "jira_tok", "2026-09", [day], [member])

        cell = metrics["m1"][day.isoformat()]
        assert cell["bitbucket"] == 1
        assert cell["jira"] == 1
        assert cell["tempo_hours"] == 2.0
        assert cell["tempo_tasks"] == ["QA-2"]
        assert warnings == []

    async def test_tempo_failure_is_a_warning_not_a_raise(self, monkeypatch):
        day = date(2026, 9, 2)
        member = _Member(id="m1")

        async def fake_reveal(cred_id: str):
            raise NotFoundError(error_code="CREDENTIAL_NOT_FOUND", message="no credential")

        async def fake_worklogs(**kwargs):
            raise ServiceUnavailableError(error_code="TEMPO_ERROR", message="Tempo returned 500")

        monkeypatch.setattr(secret_client, "reveal_credential", fake_reveal)
        monkeypatch.setattr(tempo_client, "search_worklogs", fake_worklogs)

        metrics, warnings = await svc.collect_activity(_Settings(), "jira_tok", "2026-09", [day], [member])

        assert metrics["m1"][day.isoformat()]["tempo_hours"] == 0.0
        assert any("bitbucket" in w for w in warnings)
        assert any("tempo" in w for w in warnings)

    async def test_incomplete_settings_yields_warnings_only(self):
        day = date(2026, 9, 2)
        member = _Member(id="m1")
        settings = _Settings(bitbucket_base_url=None, jira_board_id=None, tempo_team_id=None)

        metrics, warnings = await svc.collect_activity(settings, "jira_tok", "2026-09", [day], [member])

        assert metrics["m1"][day.isoformat()] == {"bitbucket": 0, "jira": 0, "tempo_hours": 0.0, "tempo_tasks": []}
        assert len(warnings) == 3


# ── generate_report / API ─────────────────────────────────────────────────────


@pytest.fixture
def mock_secret_client(monkeypatch):
    store: dict[str, tuple[str, str]] = {}

    async def fake_reveal(cred_id: str):
        if cred_id not in store:
            raise NotFoundError(error_code="CREDENTIAL_NOT_FOUND", message="not found")
        return store[cred_id]

    monkeypatch.setattr(secret_client, "reveal_credential", fake_reveal)
    return store


@pytest.fixture
def mock_confluence_pages(monkeypatch):
    calls: dict[str, list] = {"find": [], "create": [], "update": []}
    state: dict = {"existing_id": None}

    async def fake_find_page_id(*, base_url, bearer_token, space, title):
        calls["find"].append(title)
        if title == state.get("existing_title"):
            return state["existing_id"]
        return None

    async def fake_create_page(*, base_url, bearer_token, space, title, parent_id, body_html):
        calls["create"].append((title, parent_id))
        return "pg_new"

    async def fake_get_page_version(*, base_url, bearer_token, page_id):
        return 3

    async def fake_update_page(*, base_url, bearer_token, page_id, title, body_html, version):
        calls["update"].append((page_id, title, version))

    monkeypatch.setattr(confluence_client, "find_page_id", fake_find_page_id)
    monkeypatch.setattr(confluence_client, "create_page", fake_create_page)
    monkeypatch.setattr(confluence_client, "get_page_version", fake_get_page_version)
    monkeypatch.setattr(confluence_client, "update_page", fake_update_page)
    return calls, state


@pytest.fixture
def mock_empty_sources(monkeypatch):
    """Bitbucket/Jira/Tempo все отдают пустые результаты — фокус теста на Confluence/RBAC.

    Требует `mock_secret_client` в том же тесте — `cred_bitbucket` тоже должен
    быть зарегистрирован там, иначе reveal бросит `CREDENTIAL_NOT_FOUND` и
    попадёт в `warnings` (не ломает тест, но зашумляет `error` в ответе).
    """

    async def _empty(*args, **kwargs):
        return []

    monkeypatch.setattr(bitbucket_client, "get_branches", _empty)
    monkeypatch.setattr(bitbucket_client, "get_commits", _empty)
    monkeypatch.setattr(jira_report_client, "get_sprint_ids", _empty)
    monkeypatch.setattr(tempo_client, "search_worklogs", _empty)


class TestGenerateReportEndpoint:
    async def test_not_configured_returns_422(self, client, admin_token):
        resp = await client.post(
            REPORTS_BASE.format(dept="dep_unconfigured") + "/generate",
            headers=_hdr(admin_token), json={"period": "2026-09"},
        )
        # admin_token belongs to dep_a — cross-department mismatch fires first.
        assert resp.status_code == 403, resp.text

    async def test_not_configured_for_own_department_returns_422(self, client, make_token):
        token = make_token(department_id="dep_noconf", service_roles={"testing_service": ["admin"]})
        resp = await client.post(
            REPORTS_BASE.format(dept="dep_noconf") + "/generate",
            headers=_hdr(token), json={"period": "2026-09"},
        )
        assert resp.status_code == 422, resp.text
        assert resp.json()["error_code"] == "ACTIVITY_REPORT_NOT_CONFIGURED"

    async def test_generate_creates_new_page(
        self, client, make_token, mock_secret_client, mock_confluence_pages, mock_empty_sources,
    ):
        dept = "dep_gen_new"
        await _seed_settings(dept)
        mock_secret_client["cred_primary"] = ("bot", "jira_tok")
        mock_secret_client["cred_bitbucket"] = ("bb", "pass")
        token = make_token(department_id=dept, service_roles={"testing_service": ["admin"]})

        resp = await client.post(
            REPORTS_BASE.format(dept=dept) + "/generate",
            headers=_hdr(token), json={"period": "2026-09"},
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["status"] == "done"
        assert body["confluence_page_id"] == "pg_new"
        calls, _state = mock_confluence_pages
        assert calls["create"] == [("2026-09", None)]

    async def test_generate_updates_existing_page(
        self, client, make_token, mock_secret_client, mock_confluence_pages, mock_empty_sources,
    ):
        dept = "dep_gen_upd"
        await _seed_settings(dept)
        mock_secret_client["cred_primary"] = ("bot", "jira_tok")
        mock_secret_client["cred_bitbucket"] = ("bb", "pass")
        calls, state = mock_confluence_pages
        state["existing_title"] = "2026-09"
        state["existing_id"] = "pg_existing"
        token = make_token(department_id=dept, service_roles={"testing_service": ["admin"]})

        resp = await client.post(
            REPORTS_BASE.format(dept=dept) + "/generate",
            headers=_hdr(token), json={"period": "2026-09"},
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["status"] == "done"
        assert body["confluence_page_id"] == "pg_existing"
        assert calls["update"] == [("pg_existing", "2026-09", 3)]
        assert calls["create"] == []

    async def test_partial_source_failure_still_published(
        self, client, make_token, mock_secret_client, mock_confluence_pages, monkeypatch,
    ):
        dept = "dep_gen_partial"
        await _seed_settings(dept)
        mock_secret_client["cred_primary"] = ("bot", "jira_tok")
        mock_secret_client["cred_bitbucket"] = ("bb", "pass")
        token = make_token(department_id=dept, service_roles={"testing_service": ["admin"]})

        async def _empty(*args, **kwargs):
            return []

        async def _boom(*args, **kwargs):
            raise ServiceUnavailableError(error_code="TEMPO_ERROR", message="Tempo unreachable")

        monkeypatch.setattr(bitbucket_client, "get_branches", _empty)
        monkeypatch.setattr(jira_report_client, "get_sprint_ids", _empty)
        monkeypatch.setattr(tempo_client, "search_worklogs", _boom)

        resp = await client.post(
            REPORTS_BASE.format(dept=dept) + "/generate",
            headers=_hdr(token), json={"period": "2026-09"},
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["status"] == "done"
        assert "tempo" in body["error"]

    async def test_credential_reveal_failure_yields_failed(
        self, client, make_token, mock_secret_client,
    ):
        dept = "dep_gen_badcred"
        await _seed_settings(dept, credential_id="cred_missing")
        token = make_token(department_id=dept, service_roles={"testing_service": ["admin"]})

        resp = await client.post(
            REPORTS_BASE.format(dept=dept) + "/generate",
            headers=_hdr(token), json={"period": "2026-09"},
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["status"] == "failed"

    async def test_confluence_credential_id_overrides_publish_bearer(
        self, client, make_token, mock_secret_client, mock_empty_sources, monkeypatch,
    ):
        """C4: confluence_credential_id идёт в Confluence, credential_id — только в Jira/Tempo."""
        dept = "dep_gen_confcred"
        await _seed_settings(dept, confluence_credential_id="cred_confluence")
        mock_secret_client["cred_primary"] = ("bot", "jira_tok")
        mock_secret_client["cred_bitbucket"] = ("bb", "pass")
        mock_secret_client["cred_confluence"] = ("bot", "confluence_tok")

        seen_bearers: list[str] = []

        async def fake_find_page_id(*, base_url, bearer_token, space, title):
            seen_bearers.append(bearer_token)
            return None

        async def fake_create_page(*, base_url, bearer_token, space, title, parent_id, body_html):
            seen_bearers.append(bearer_token)
            return "pg_new"

        monkeypatch.setattr(confluence_client, "find_page_id", fake_find_page_id)
        monkeypatch.setattr(confluence_client, "create_page", fake_create_page)

        token = make_token(department_id=dept, service_roles={"testing_service": ["admin"]})
        resp = await client.post(
            REPORTS_BASE.format(dept=dept) + "/generate",
            headers=_hdr(token), json={"period": "2026-09"},
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["status"] == "done"
        assert seen_bearers and all(bearer == "confluence_tok" for bearer in seen_bearers)

    async def test_department_admin_bypasses_matrix(
        self, client, make_token, mock_secret_client, mock_confluence_pages, mock_empty_sources,
    ):
        dept = "dep_gen_deptadmin"
        await _seed_settings(dept)
        mock_secret_client["cred_primary"] = ("bot", "jira_tok")
        mock_secret_client["cred_bitbucket"] = ("bb", "pass")
        token = make_token(department_id=dept, platform_role="department_admin")

        resp = await client.post(
            REPORTS_BASE.format(dept=dept) + "/generate",
            headers=_hdr(token), json={"period": "2026-09"},
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["status"] == "done"

    async def test_guest_role_forbidden(self, client, make_token):
        dept = "dep_gen_guest"
        await _seed_settings(dept)
        token = make_token(department_id=dept, service_roles={"testing_service": ["guest"]})

        resp = await client.post(
            REPORTS_BASE.format(dept=dept) + "/generate",
            headers=_hdr(token), json={"period": "2026-09"},
        )
        assert resp.status_code == 403, resp.text

    async def test_cross_department_admin_forbidden(self, client, make_token):
        dept = "dep_gen_target"
        await _seed_settings(dept)
        token = make_token(department_id="dep_other", service_roles={"testing_service": ["admin"]})

        resp = await client.post(
            REPORTS_BASE.format(dept=dept) + "/generate",
            headers=_hdr(token), json={"period": "2026-09"},
        )
        assert resp.status_code == 403, resp.text

    async def test_anonymous_401(self, client):
        resp = await client.post(
            REPORTS_BASE.format(dept="dep_a") + "/generate", json={"period": "2026-09"},
        )
        assert resp.status_code == 401, resp.text


class TestListActivityReports:
    async def test_lists_previous_generations(
        self, client, make_token, mock_secret_client, mock_confluence_pages, mock_empty_sources,
    ):
        dept = "dep_list"
        await _seed_settings(dept)
        mock_secret_client["cred_primary"] = ("bot", "jira_tok")
        mock_secret_client["cred_bitbucket"] = ("bb", "pass")
        token = make_token(department_id=dept, service_roles={"testing_service": ["admin"]})

        await client.post(
            REPORTS_BASE.format(dept=dept) + "/generate",
            headers=_hdr(token), json={"period": "2026-09"},
        )
        resp = await client.get(REPORTS_BASE.format(dept=dept), headers=_hdr(token))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total"] == 1
        assert body["items"][0]["period"] == "2026-09"

    async def test_cross_department_forbidden(self, client, make_token):
        dept = "dep_list_target"
        token = make_token(department_id="dep_other", service_roles={"testing_service": ["admin"]})
        resp = await client.get(REPORTS_BASE.format(dept=dept), headers=_hdr(token))
        assert resp.status_code == 403, resp.text

    async def test_anonymous_401(self, client):
        resp = await client.get(REPORTS_BASE.format(dept="dep_a"))
        assert resp.status_code == 401, resp.text
