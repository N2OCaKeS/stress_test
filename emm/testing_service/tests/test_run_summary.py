"""Тесты end-of-run комментария в Confluence-блоге (§2.7, §9.2 плана миграции).

`render_titles` — чистая функция, тестируется напрямую. `post_run_summary` —
все внешние вызовы (`secret_client.reveal_credential`, `confluence_client.*`)
мокаются monkeypatch'ем модульных функций, ни одного реального сетевого
вызова. Последний класс (`TestQueueTrigger`) проверяет, что событийный триггер
из `services/queue.py` действительно срабатывает на терминальном переходе
кампании — переиспользует фикстуры/хелперы `tests.test_queue`/`tests.test_test_runs`.
"""

from __future__ import annotations

import pytest

from src.core.constants import RunSummaryCommentStatus
from src.db.session import AsyncSessionLocal
from src.repositories import department_integration_settings as dis_repo
from src.repositories import run_summary_comment as rsc_repo
from src.repositories import test_run as test_run_repo
from src.services import confluence_client, run_summary as run_summary_svc, secret_client
from src.utils.ids import department_integration_settings_id
from src.utils.ids import run_summary_comment_id as new_rsc_id
from src.utils.ids import test_run_id as new_test_run_id
from tests.conftest import auth_hdr as _hdr
from tests.test_queue import (
    CALLBACK_BASE,  # noqa: F401 — реэкспорт для TestQueueTrigger
    QUEUE_BASE,  # noqa: F401
    SERVER_SECRET,
    WORKER_SECRET,
    _create_stand,
    _create_test_def,
    _server_hdr,  # noqa: F401
    configure_internal_keys,
    mock_server_service,
    recorded_calls,  # noqa: F401 — mock_server_service объявляет её как свою зависимость
)
from tests.test_test_runs import _drive_to_success, _get_item, _payload

RUNS_BASE = "/api/testing/v1/test-runs"


async def _seed_test_run(*, department_id="dep_a", os_version_id="1.8.5.46", status="succeeded") -> str:
    async with AsyncSessionLocal() as db:
        run = await test_run_repo.create(db, {
            "id": new_test_run_id(),
            "os_version_id": os_version_id,
            "mode": "orel",
            "kernel": "6.1.0",
            "department_id": department_id,
            "test_run_stands": [],
            "status": status,
            "final": False,
            "created_by": "usr_test",
        })
        await db.commit()
        return run.id


async def _seed_integration_settings(
    department_id: str, *, credential_id: str = "cred_x", confluence_base_url: str = "http://confluence.example",
    bitbucket_credential_id: str | None = None, confluence_credential_id: str | None = None,
) -> None:
    async with AsyncSessionLocal() as db:
        await dis_repo.create(db, {
            "id": department_integration_settings_id(),
            "department_id": department_id,
            "credential_id": credential_id,
            "confluence_credential_id": confluence_credential_id,
            "jira_base_url": None,
            "confluence_base_url": confluence_base_url,
            "bitbucket_credential_id": bitbucket_credential_id,
        })
        await db.commit()


@pytest.fixture
def mock_secret_client(monkeypatch):
    """`{cred_id: (login, secret)}` — 404/403 модельируются отсутствием ключа."""
    store: dict[str, tuple[str, str]] = {}

    async def fake_reveal(cred_id: str):
        if cred_id not in store:
            from src.core.exceptions import NotFoundError

            raise NotFoundError(error_code="CREDENTIAL_NOT_FOUND", message="not found")
        return store[cred_id]

    monkeypatch.setattr(secret_client, "reveal_credential", fake_reveal)
    return store


@pytest.fixture
def mock_confluence(monkeypatch):
    """Мокает все пять функций `confluence_client` разом. Возвращает `(calls, state)`.

    `state["page_id"]`/`state["blog_id"]` — что находит find_page_id/
    find_blogpost_id (`None` моделирует "не найдено"). `state["comments"]` —
    список уже существующих комментариев блог-поста для `get_comments`/
    `update_comment`.
    """
    calls: dict[str, list] = {
        "find_page": [], "find_blog": [], "get_comments": [], "add_comment": [], "update_comment": [],
    }
    state: dict = {"page_id": "pg_1", "blog_id": "blog_1", "comments": []}

    async def fake_find_page_id(*, base_url, bearer_token, space, title):
        calls["find_page"].append(title)
        return state["page_id"]

    async def fake_find_blogpost_id(*, base_url, bearer_token, space, title):
        calls["find_blog"].append(title)
        return state["blog_id"]

    async def fake_get_comments(*, base_url, bearer_token, content_id):
        calls["get_comments"].append(content_id)
        return state["comments"]

    async def fake_add_comment(*, base_url, bearer_token, content_id, body_html, container_type="blogpost"):
        calls["add_comment"].append(body_html)
        comment_id = f"cmt_{len(calls['add_comment'])}"
        state["comments"].append({"id": comment_id, "version": {"number": 1}})
        return comment_id

    async def fake_update_comment(*, base_url, bearer_token, comment_id, body_html, version):
        calls["update_comment"].append((comment_id, body_html, version))
        for c in state["comments"]:
            if c["id"] == comment_id:
                c["version"] = {"number": version + 1}

    monkeypatch.setattr(confluence_client, "find_page_id", fake_find_page_id)
    monkeypatch.setattr(confluence_client, "find_blogpost_id", fake_find_blogpost_id)
    monkeypatch.setattr(confluence_client, "get_comments", fake_get_comments)
    monkeypatch.setattr(confluence_client, "add_comment", fake_add_comment)
    monkeypatch.setattr(confluence_client, "update_comment", fake_update_comment)
    return calls, state


# ── render_titles ─────────────────────────────────────────────────────────────


class TestRenderTitles:
    def test_ordinary_release_four_segments(self):
        stp_title, blog_title = run_summary_svc.render_titles("1.8.5.46")
        assert stp_title == "STRESS_report ⬝ 1.8.5"
        assert blog_title == "1.8.5.46 оперативного обновления Astra Linux SE 1.8.5"

    def test_hotfix_six_segments_with_uu_marker(self):
        stp_title, blog_title = run_summary_svc.render_titles("1.8.5.UU.46.1")
        assert stp_title == "STRESS_report ⬝ 1.8.5.46"
        assert blog_title == "1.8.5.UU.46.1 срочного обновления Astra Linux SE 1.8.5.46"

    def test_six_segments_without_uu_marker_is_treated_as_ordinary(self):
        # 4-й сегмент не "UU" — не хотфикс-формат, дефолтная ветка.
        stp_title, blog_title = run_summary_svc.render_titles("1.8.5.46.7.8")
        assert stp_title == "STRESS_report ⬝ 1.8.5"
        assert "оперативного обновления" in blog_title

    def test_unusual_short_format_falls_back_without_raising(self):
        stp_title, blog_title = run_summary_svc.render_titles("1.8")
        assert stp_title == "STRESS_report ⬝ 1.8"
        assert blog_title == "1.8 оперативного обновления Astra Linux SE 1.8"


# ── _resolve_confluence_bearer ───────────────────────────────────────────────


class TestResolveConfluenceBearer:
    async def test_confluence_credential_id_overrides_credential_id(self, mock_secret_client):
        mock_secret_client["cred_x"] = ("bot", "jira_tok")
        mock_secret_client["cred_confluence"] = ("bot", "confluence_tok")
        await _seed_integration_settings("dep_conf_override", confluence_credential_id="cred_confluence")
        async with AsyncSessionLocal() as db:
            result = await run_summary_svc._resolve_confluence_bearer(db, "dep_conf_override")
        assert result is not None
        _base_url, secret = result
        assert secret == "confluence_tok"

    async def test_falls_back_to_credential_id_when_unset(self, mock_secret_client):
        mock_secret_client["cred_x"] = ("bot", "jira_tok")
        await _seed_integration_settings("dep_conf_fallback")
        async with AsyncSessionLocal() as db:
            result = await run_summary_svc._resolve_confluence_bearer(db, "dep_conf_fallback")
        assert result is not None
        _base_url, secret = result
        assert secret == "jira_tok"


# ── post_run_summary ─────────────────────────────────────────────────────────


class TestPostRunSummary:
    async def test_no_integration_settings_yields_failed(self):
        run_id = await _seed_test_run(department_id="dep_none")
        async with AsyncSessionLocal() as db:
            result = await run_summary_svc.post_run_summary(db, run_id)
        assert result.status == RunSummaryCommentStatus.FAILED

    async def test_reveal_credential_failure_yields_failed(self, mock_secret_client):
        run_id = await _seed_test_run(department_id="dep_badcred")
        await _seed_integration_settings("dep_badcred", credential_id="cred_missing")
        async with AsyncSessionLocal() as db:
            result = await run_summary_svc.post_run_summary(db, run_id)
        assert result.status == RunSummaryCommentStatus.FAILED

    async def test_missing_test_run_returns_none(self):
        async with AsyncSessionLocal() as db:
            result = await run_summary_svc.post_run_summary(db, "run_does_not_exist")
        assert result is None

    async def test_stp_page_not_found_yields_skipped(self, mock_confluence, mock_secret_client):
        _calls, state = mock_confluence
        state["page_id"] = None
        mock_secret_client["cred_x"] = ("bot", "tok123")
        run_id = await _seed_test_run(department_id="dep_nopage")
        await _seed_integration_settings("dep_nopage")

        async with AsyncSessionLocal() as db:
            result = await run_summary_svc.post_run_summary(db, run_id)
        assert result.status == RunSummaryCommentStatus.SKIPPED_NO_STP_PAGE
        assert result.confluence_blog_id is None

    async def test_blog_not_found_yields_skipped(self, mock_confluence, mock_secret_client):
        _calls, state = mock_confluence
        state["blog_id"] = None
        mock_secret_client["cred_x"] = ("bot", "tok123")
        run_id = await _seed_test_run(department_id="dep_noblog")
        await _seed_integration_settings("dep_noblog")

        async with AsyncSessionLocal() as db:
            result = await run_summary_svc.post_run_summary(db, run_id)
        assert result.status == RunSummaryCommentStatus.SKIPPED_NO_BLOG
        assert result.stp_page_id == state["page_id"]
        assert result.confluence_blog_id is None

    async def test_first_post_creates_comment(self, mock_confluence, mock_secret_client):
        calls, state = mock_confluence
        mock_secret_client["cred_x"] = ("bot", "tok123")
        run_id = await _seed_test_run(department_id="dep_new")
        await _seed_integration_settings("dep_new")

        async with AsyncSessionLocal() as db:
            result = await run_summary_svc.post_run_summary(db, run_id)

        assert result.status == RunSummaryCommentStatus.POSTED
        assert result.confluence_blog_id == state["blog_id"]
        assert result.stp_page_id == state["page_id"]
        assert result.confluence_comment_id is not None
        assert result.posted_at is not None
        assert len(calls["add_comment"]) == 1
        assert "Нагрузочное тестирование" in calls["add_comment"][0]
        assert len(calls["update_comment"]) == 0

    async def test_unchanged_body_is_a_noop(self, mock_confluence, mock_secret_client):
        calls, state = mock_confluence
        mock_secret_client["cred_x"] = ("bot", "tok123")
        run_id = await _seed_test_run(department_id="dep_noop")
        await _seed_integration_settings("dep_noop")

        async with AsyncSessionLocal() as db:
            first = await run_summary_svc.post_run_summary(db, run_id)
        assert first.status == RunSummaryCommentStatus.POSTED
        assert len(calls["add_comment"]) == 1

        async with AsyncSessionLocal() as db:
            second = await run_summary_svc.post_run_summary(db, run_id)

        assert second.status == RunSummaryCommentStatus.POSTED
        assert second.confluence_comment_id == first.confluence_comment_id
        # Тело не изменилось — ни add, ни update, ни даже get_comments не вызывались повторно.
        assert len(calls["add_comment"]) == 1
        assert len(calls["update_comment"]) == 0
        assert len(calls["get_comments"]) == 0

    async def test_changed_body_triggers_update(self, mock_confluence, mock_secret_client):
        calls, state = mock_confluence
        mock_secret_client["cred_x"] = ("bot", "tok123")
        run_id = await _seed_test_run(department_id="dep_upd")
        await _seed_integration_settings("dep_upd")

        async with AsyncSessionLocal() as db:
            existing = await rsc_repo.create(db, {
                "id": new_rsc_id(),
                "test_run_id": run_id,
                "confluence_blog_id": state["blog_id"],
                "confluence_comment_id": "cmt_old",
                "stp_page_id": state["page_id"],
                "status": RunSummaryCommentStatus.POSTED,
                "body_snapshot": "<p>stale body from a previous run</p>",
            })
            await db.commit()
        state["comments"].append({"id": "cmt_old", "version": {"number": 3}})

        async with AsyncSessionLocal() as db:
            result = await run_summary_svc.post_run_summary(db, run_id)

        assert result.id == existing.id
        assert result.status == RunSummaryCommentStatus.POSTED
        assert result.confluence_comment_id == "cmt_old"
        assert result.body_snapshot != "<p>stale body from a previous run</p>"
        assert len(calls["add_comment"]) == 0
        assert calls["update_comment"] == [
            ("cmt_old", result.body_snapshot, 3),
        ]

    async def test_comment_deleted_upstream_is_recreated(self, mock_confluence, mock_secret_client):
        """Комментарий пропал на стороне Confluence (удалён вручную) — заводим заново, не падаем."""
        calls, state = mock_confluence
        mock_secret_client["cred_x"] = ("bot", "tok123")
        run_id = await _seed_test_run(department_id="dep_gone")
        await _seed_integration_settings("dep_gone")

        async with AsyncSessionLocal() as db:
            await rsc_repo.create(db, {
                "id": new_rsc_id(),
                "test_run_id": run_id,
                "confluence_blog_id": state["blog_id"],
                "confluence_comment_id": "cmt_vanished",
                "stp_page_id": state["page_id"],
                "status": RunSummaryCommentStatus.POSTED,
                "body_snapshot": "<p>stale body</p>",
            })
            await db.commit()
        # state["comments"] остаётся пустым — "cmt_vanished" не найдётся.

        async with AsyncSessionLocal() as db:
            result = await run_summary_svc.post_run_summary(db, run_id)

        assert result.status == RunSummaryCommentStatus.POSTED
        assert result.confluence_comment_id != "cmt_vanished"
        assert len(calls["add_comment"]) == 1
        assert len(calls["update_comment"]) == 0


# ── GET /test-runs/{id}/summary-comment ──────────────────────────────────────


class TestGetRunSummaryCommentEndpoint:
    async def test_returns_empty_before_any_attempt(self, client, admin_token):
        run_id = await _seed_test_run(department_id="dep_a")
        resp = await client.get(f"{RUNS_BASE}/{run_id}/summary-comment", headers=_hdr(admin_token))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["test_run_id"] == run_id
        assert body["status"] is None
        assert body["confluence_comment_id"] is None

    async def test_404_for_missing_run(self, client, admin_token):
        resp = await client.get(f"{RUNS_BASE}/run_does_not_exist/summary-comment", headers=_hdr(admin_token))
        assert resp.status_code == 404, resp.text

    async def test_requires_auth(self, client):
        resp = await client.get(f"{RUNS_BASE}/run_x/summary-comment")
        assert resp.status_code == 401, resp.text

    async def test_reflects_posted_state(self, client, admin_token, mock_confluence, mock_secret_client):
        _calls, _state = mock_confluence
        mock_secret_client["cred_x"] = ("bot", "tok123")
        run_id = await _seed_test_run(department_id="dep_get")
        await _seed_integration_settings("dep_get")

        async with AsyncSessionLocal() as db:
            await run_summary_svc.post_run_summary(db, run_id)

        resp = await client.get(f"{RUNS_BASE}/{run_id}/summary-comment", headers=_hdr(admin_token))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "posted"
        assert body["confluence_blog_id"] == "blog_1"
        assert body["posted_at"] is not None


# ── Триггер из services/queue.py на терминальном переходе кампании ──────────


class TestQueueTrigger:
    async def test_campaign_success_triggers_run_summary(
        self, client, admin_token, mock_server_service, configure_internal_keys,
        mock_confluence, mock_secret_client,
    ):
        calls, _state = mock_confluence
        mock_secret_client["cred_x"] = ("bot", "tok123")
        mock_secret_client["cred_bitbucket"] = ("git-bot", "git-token")
        mock_server_service()
        stand_id, _ = await _create_stand(client, admin_token, department_id="dep_a")
        await _create_test_def(client, admin_token, stand_id)
        await _seed_integration_settings("dep_a", bitbucket_credential_id="cred_bitbucket")

        resp = await client.post(RUNS_BASE, headers=_hdr(admin_token), json=_payload([stand_id]))
        assert resp.status_code == 201, resp.text
        run_id = resp.json()["id"]
        detail = await client.get(f"{RUNS_BASE}/{run_id}", headers=_hdr(admin_token))
        item_id = detail.json()["queue_items"][0]["queue_item_id"]
        item = await _get_item(item_id)

        await _drive_to_success(client, item)

        summary = await client.get(f"{RUNS_BASE}/{run_id}/summary-comment", headers=_hdr(admin_token))
        assert summary.status_code == 200, summary.text
        assert summary.json()["status"] == "posted"
        assert len(calls["add_comment"]) == 1

    async def test_non_terminal_transition_does_not_trigger(
        self, client, admin_token, mock_server_service, configure_internal_keys,
        mock_confluence, mock_secret_client,
    ):
        """`preparing`/`ready` — не терминал, `_maybe_post_run_summary` не должна дёргаться."""
        calls, _state = mock_confluence
        mock_secret_client["cred_x"] = ("bot", "tok123")
        mock_server_service()
        stand_id, _ = await _create_stand(client, admin_token, department_id="dep_a")
        await _create_test_def(client, admin_token, stand_id)
        await _seed_integration_settings("dep_a")

        resp = await client.post(RUNS_BASE, headers=_hdr(admin_token), json=_payload([stand_id]))
        run_id = resp.json()["id"]

        summary = await client.get(f"{RUNS_BASE}/{run_id}/summary-comment", headers=_hdr(admin_token))
        assert summary.json()["status"] is None
        assert calls["add_comment"] == []
