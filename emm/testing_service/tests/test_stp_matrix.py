"""Тесты публикации СТП-матрицы в Confluence (§D2/D3 плана миграции).

`_hierarchy_titles`/`render_matrix_html` — чистые функции, тестируются
напрямую. `publish_stp_matrix` — все внешние вызовы (`secret_client.
reveal_credential`, `confluence_client.*`) мокаются monkeypatch'ем модульных
функций, ни одного реального сетевого вызова. Данные (`stp_test_runs`/
`stp_cells`/`stp_test_cases`/`test_stands`) заводятся напрямую через
репозитории — публикация читает уже существующие данные, не генерирует их
(генерация уже покрыта `tests/test_stp.py`).
"""

from __future__ import annotations

import uuid

import pytest

from src.core.constants import StpMatrixPublicationStatus
from src.db.session import AsyncSessionLocal
from src.repositories import department_integration_settings as dis_repo
from src.repositories import stp_cell as stp_cell_repo
from src.repositories import stp_test_case as stp_test_case_repo
from src.repositories import stp_test_run as stp_test_run_repo
from src.repositories import test_stand as test_stand_repo
from src.services import confluence_client, secret_client, server_client, stp_matrix as stp_matrix_svc
from src.utils.ids import department_integration_settings_id
from src.utils.ids import stp_cell_id, stp_test_case_id, stp_test_run_id
from src.utils.ids import test_stand_id as new_test_stand_id
from tests.conftest import auth_hdr as _hdr

MATRIX_BASE = "/api/testing/v1/stp/matrix/publish"
DIS_BASE = "/api/testing/v1/department-integration-settings"


async def _seed_stand(department_id: str) -> str:
    async with AsyncSessionLocal() as db:
        stand = await test_stand_repo.create(db, {
            "id": new_test_stand_id(),
            "server_id": f"srv_{uuid.uuid4().hex[:10]}",
            "department_id": department_id,
        })
        await db.commit()
        return stand.id


async def _seed_case(code: str, title: str) -> str:
    async with AsyncSessionLocal() as db:
        case = await stp_test_case_repo.create(db, {
            "id": stp_test_case_id(), "code": code, "title": title, "zephyr_id": "BT-T1",
            "department_id": None, "created_by": "usr_test",
        })
        await db.commit()
        return case.id


async def _seed_run(*, os_version_id: str, mode: str, kernel: str, stand_id: str) -> str:
    async with AsyncSessionLocal() as db:
        run = await stp_test_run_repo.create(db, {
            "id": stp_test_run_id(), "os_version_id": os_version_id, "mode": mode,
            "kernel": kernel, "stand_id": stand_id,
            "zephyr_test_run_key": "BT-R1", "zephyr_folder_path": "/x",
        })
        await db.commit()
        return run.id


async def _seed_cell(*, case_id: str, run_id: str, status: str, is_active: bool = True) -> str:
    async with AsyncSessionLocal() as db:
        cell = await stp_cell_repo.create(db, {
            "id": stp_cell_id(), "stp_test_case_id": case_id, "stp_test_run_id": run_id,
            "status": status, "is_active": is_active,
        })
        await db.commit()
        return cell.id


async def _seed_integration_settings(
    department_id: str, *, credential_id="cred_x", confluence_base_url="http://confluence.example",
    stp_matrix_confluence_space="DEPTQA", stp_matrix_confluence_root_page_title="Состав тестового прогона",
    confluence_credential_id=None,
) -> None:
    async with AsyncSessionLocal() as db:
        await dis_repo.create(db, {
            "id": department_integration_settings_id(),
            "department_id": department_id,
            "credential_id": credential_id,
            "confluence_credential_id": confluence_credential_id,
            "jira_base_url": None,
            "confluence_base_url": confluence_base_url,
            "stp_matrix_confluence_space": stp_matrix_confluence_space,
            "stp_matrix_confluence_root_page_title": stp_matrix_confluence_root_page_title,
        })
        await db.commit()


@pytest.fixture(autouse=True)
def mock_os_version_catalog(monkeypatch):
    """`{os_version_id: name}` — карточка версии из server_service.

    Autouse: заголовки иерархии и тело матрицы строятся из НОМЕРА РЦ, а
    `stp_test_runs.os_version_id` — id каталога. Дефолт `name == id`
    сохраняет старые фикстуры, где id уже записан человеческой версией.
    """
    names: dict[str, str] = {}

    async def fake_get_os_version(os_version_id: str) -> dict:
        return {"id": os_version_id, "name": names.get(os_version_id, os_version_id)}

    monkeypatch.setattr(server_client, "get_os_version", fake_get_os_version)
    return names


@pytest.fixture
def mock_secret_client(monkeypatch):
    store: dict[str, tuple[str, str]] = {}

    async def fake_reveal(cred_id: str):
        if cred_id not in store:
            from src.core.exceptions import NotFoundError
            raise NotFoundError(error_code="CREDENTIAL_NOT_FOUND", message="not found")
        return store[cred_id]

    monkeypatch.setattr(secret_client, "reveal_credential", fake_reveal)
    return store


@pytest.fixture
def mock_confluence_pages(monkeypatch):
    """Confluence-страницы в памяти, ключ — id. `calls` считает вызовы по типу."""
    pages: dict[str, dict] = {}
    calls: dict[str, list] = {"find": [], "create": [], "get_version": [], "update": []}

    async def fake_find_page_id(*, base_url, bearer_token, space, title):
        calls["find"].append(title)
        for pid, p in pages.items():
            if p["title"] == title:
                return pid
        return None

    async def fake_create_page(*, base_url, bearer_token, space, title, parent_id, body_html):
        calls["create"].append(title)
        pid = f"pg_{len(pages) + 1}"
        pages[pid] = {"title": title, "body": body_html, "version": 1, "parent_id": parent_id}
        return pid

    async def fake_get_page_version(*, base_url, bearer_token, page_id):
        calls["get_version"].append(page_id)
        return pages[page_id]["version"]

    async def fake_update_page(*, base_url, bearer_token, page_id, title, body_html, version):
        calls["update"].append((page_id, body_html, version))
        pages[page_id]["body"] = body_html
        pages[page_id]["version"] = version + 1

    monkeypatch.setattr(confluence_client, "find_page_id", fake_find_page_id)
    monkeypatch.setattr(confluence_client, "create_page", fake_create_page)
    monkeypatch.setattr(confluence_client, "get_page_version", fake_get_page_version)
    monkeypatch.setattr(confluence_client, "update_page", fake_update_page)
    return calls, pages


# ── _hierarchy_titles ────────────────────────────────────────────────────────


class TestHierarchyTitles:
    def test_ordinary_four_segment_release(self):
        parent, page = stp_matrix_svc._hierarchy_titles("1.8.5.46")
        assert parent == "STRESS_stp ⬝ 1.8.5"
        assert page == "1.8.5.46"

    def test_hotfix_six_segments_with_uu_marker(self):
        parent, page = stp_matrix_svc._hierarchy_titles("1.8.5.UU.46.1")
        assert parent == "STRESS_stp ⬝ 1.8.5.46"
        assert page == "1.8.5.UU.46.1"

    def test_unusual_format_is_flat_under_grandparent(self):
        parent, page = stp_matrix_svc._hierarchy_titles("1.8")
        assert parent is None
        assert page == "STRESS_stp ⬝ 1.8"


# ── render_matrix_html ───────────────────────────────────────────────────────


class TestRenderMatrixHtml:
    def test_renders_headers_and_colored_statuses(self):
        from src.models import StpCell, StpTestCase, StpTestRun

        run = StpTestRun(
            id="run_1", os_version_id="1.8.5.46", mode="orel", kernel="6.1.0", stand_id="stand_1",
        )
        case = StpTestCase(id="case_1", code="pg", title="PostgreSQL")
        cell = StpCell(id="cell_1", stp_test_case_id="case_1", stp_test_run_id="run_1", status="pass")

        html = stp_matrix_svc.render_matrix_html(
            rc_number="1.8.5.46", runs=[run], cases=[case], cells=[cell],
        )
        assert "Прогресс выполнения тестового прогона 1.8.5.46" in html
        assert "PostgreSQL" in html
        assert "Выполнено" in html
        assert "#dafee6" in html
        assert "#e4f1fc" in html  # подсветка режима orel

    def test_stand_row_uses_alias_and_sorts_by_it(self):
        """«№ стенда» — человеческое имя, и по нему же порядок столбцов.

        Легаси упорядочивал прогоны строкой имени стенда; сортировка по
        внутреннему uuid случайна и меняется от отдела к отделу.
        """
        from src.models import StpTestCase, StpTestRun

        run_b = StpTestRun(
            id="run_b", os_version_id="1.8.5.46", mode="orel", kernel="6.1.0", stand_id="stand_bbb",
        )
        run_a = StpTestRun(
            id="run_a", os_version_id="1.8.5.46", mode="orel", kernel="6.1.0", stand_id="stand_aaa",
        )
        case = StpTestCase(id="case_1", code="pg", title="PostgreSQL")

        html = stp_matrix_svc.render_matrix_html(
            rc_number="1.8.5.46", runs=[run_b, run_a], cases=[case], cells=[],
            stand_labels={"stand_bbb": "stand4", "stand_aaa": "stand10"},
        )
        assert "stand_bbb" not in html
        assert "stand_aaa" not in html
        # stand10 < stand4 лексикографически — ровно как у легаси
        assert html.index("stand10") < html.index("stand4")

    def test_stand_without_alias_falls_back_to_internal_id(self):
        from src.models import StpTestCase, StpTestRun

        run = StpTestRun(
            id="run_1", os_version_id="1.8.5.46", mode="orel", kernel="6.1.0", stand_id="stand_zzz",
        )
        case = StpTestCase(id="case_1", code="pg", title="PostgreSQL")
        html = stp_matrix_svc.render_matrix_html(
            rc_number="1.8.5.46", runs=[run], cases=[case], cells=[], stand_labels={},
        )
        assert "stand_zzz" in html

    def test_no_runs_yields_placeholder(self):
        html = stp_matrix_svc.render_matrix_html(
            rc_number="1.8.5.46", runs=[], cases=[], cells=[],
        )
        assert "Нет прогонов" in html

    def test_missing_cell_renders_blank(self):
        from src.models import StpTestCase, StpTestRun

        run = StpTestRun(
            id="run_1", os_version_id="1.8.5.46", mode="smolensk", kernel="6.1.0", stand_id="stand_1",
        )
        case = StpTestCase(id="case_1", code="pg", title="PostgreSQL")
        html = stp_matrix_svc.render_matrix_html(
            rc_number="1.8.5.46", runs=[run], cases=[case], cells=[],
        )
        assert "#ffe8e8" in html  # подсветка режима smolensk
        assert "Не запускался" not in html  # ячейка без cell — пусто, не "not_run"


# ── publish_stp_matrix ───────────────────────────────────────────────────────


class TestPublishStpMatrix:
    async def test_not_configured_yields_skip_status(self, dept_a):
        async with AsyncSessionLocal() as db:
            from tests.test_queue import _identity
            result = await stp_matrix_svc.publish_stp_matrix(
                db, _identity(department_id=dept_a),
                department_id=dept_a, os_version_id="1.8.5.46",
            )
        assert result.status == StpMatrixPublicationStatus.SKIPPED_NOT_CONFIGURED

    async def test_reveal_failure_yields_skip_status(self, dept_a, mock_secret_client):
        await _seed_integration_settings(dept_a, credential_id="cred_missing")
        async with AsyncSessionLocal() as db:
            from tests.test_queue import _identity
            result = await stp_matrix_svc.publish_stp_matrix(
                db, _identity(department_id=dept_a),
                department_id=dept_a, os_version_id="1.8.5.46",
            )
        assert result.status == StpMatrixPublicationStatus.SKIPPED_NOT_CONFIGURED

    async def test_no_test_runs_yields_skip_status(self, dept_a, mock_secret_client):
        mock_secret_client["cred_x"] = ("bot", "tok123")
        await _seed_integration_settings(dept_a)
        async with AsyncSessionLocal() as db:
            from tests.test_queue import _identity
            result = await stp_matrix_svc.publish_stp_matrix(
                db, _identity(department_id=dept_a),
                department_id=dept_a, os_version_id="1.8.5.46",
            )
        assert result.status == StpMatrixPublicationStatus.SKIPPED_NO_TEST_RUNS

    async def test_confluence_credential_id_overrides_bearer(self, dept_a, mock_secret_client):
        """C4: confluence_credential_id имеет приоритет над credential_id для Confluence-контекста."""
        mock_secret_client["cred_x"] = ("bot", "jira_tok")
        mock_secret_client["cred_confluence"] = ("bot", "confluence_tok")
        await _seed_integration_settings(dept_a, confluence_credential_id="cred_confluence")
        async with AsyncSessionLocal() as db:
            ctx = await stp_matrix_svc._resolve_confluence_ctx(db, dept_a)
        assert ctx is not None
        _base_url, bearer_token, _space, _root_title = ctx
        assert bearer_token == "confluence_tok"

    async def test_confluence_credential_id_falls_back_to_credential_id(self, dept_a, mock_secret_client):
        mock_secret_client["cred_x"] = ("bot", "jira_tok")
        await _seed_integration_settings(dept_a)
        async with AsyncSessionLocal() as db:
            ctx = await stp_matrix_svc._resolve_confluence_ctx(db, dept_a)
        assert ctx is not None
        _base_url, bearer_token, _space, _root_title = ctx
        assert bearer_token == "jira_tok"

    async def test_full_publish_creates_hierarchy(self, dept_a, mock_secret_client, mock_confluence_pages):
        calls, pages = mock_confluence_pages
        mock_secret_client["cred_x"] = ("bot", "tok123")
        await _seed_integration_settings(dept_a)
        stand_id = await _seed_stand(dept_a)
        case_id = await _seed_case("pg", "PostgreSQL")
        run_id = await _seed_run(os_version_id="1.8.5.46", mode="orel", kernel="6.1.0", stand_id=stand_id)
        await _seed_cell(case_id=case_id, run_id=run_id, status="pass")

        async with AsyncSessionLocal() as db:
            from tests.test_queue import _identity
            result = await stp_matrix_svc.publish_stp_matrix(
                db, _identity(department_id=dept_a),
                department_id=dept_a, os_version_id="1.8.5.46",
            )

        assert result.status == StpMatrixPublicationStatus.POSTED
        assert result.confluence_page_id is not None
        assert result.confluence_parent_page_id is not None
        assert "PostgreSQL" in result.body_snapshot
        # grandparent + parent + сама страница — три create_page.
        assert calls["create"] == [
            "Состав тестового прогона", "STRESS_stp ⬝ 1.8.5", "1.8.5.46",
        ]
        assert pages[result.confluence_page_id]["parent_id"] == result.confluence_parent_page_id

    async def test_deactivated_cells_are_excluded_from_matrix(
        self, dept_a, mock_secret_client, mock_confluence_pages,
    ):
        """Деактивированная ячейка — исключённая из состава, но сохранённая
        история; в публикуемую матрицу попадать не должна."""
        mock_secret_client["cred_x"] = ("bot", "tok123")
        await _seed_integration_settings(dept_a)
        stand_id = await _seed_stand(dept_a)
        active_case = await _seed_case("pg", "PostgreSQL")
        inactive_case = await _seed_case("fio", "fio")
        run_id = await _seed_run(os_version_id="1.8.5.46", mode="orel", kernel="6.1.0", stand_id=stand_id)
        await _seed_cell(case_id=active_case, run_id=run_id, status="pass")
        await _seed_cell(case_id=inactive_case, run_id=run_id, status="pass", is_active=False)

        async with AsyncSessionLocal() as db:
            from tests.test_queue import _identity
            result = await stp_matrix_svc.publish_stp_matrix(
                db, _identity(department_id=dept_a),
                department_id=dept_a, os_version_id="1.8.5.46",
            )

        assert result.status == StpMatrixPublicationStatus.POSTED
        assert "PostgreSQL" in result.body_snapshot
        assert "fio" not in result.body_snapshot

    async def test_catalog_id_is_resolved_to_the_version_string(
        self, dept_a, mock_secret_client, mock_confluence_pages, mock_os_version_catalog,
    ):
        """`stp_test_runs.os_version_id` — внутренний id; в заголовки страниц
        и в тело таблицы обязан попадать номер РЦ, не `osv_<hex>`."""
        calls, _pages = mock_confluence_pages
        mock_secret_client["cred_x"] = ("bot", "tok123")
        os_version_id = "osv_3f2a1b4c5d6e7f8091a2b3c4d5e6f701"
        mock_os_version_catalog[os_version_id] = "1.8.6.38"
        await _seed_integration_settings(dept_a)
        stand_id = await _seed_stand(dept_a)
        case_id = await _seed_case("pg", "PostgreSQL")
        run_id = await _seed_run(
            os_version_id=os_version_id, mode="orel", kernel="6.1.0", stand_id=stand_id,
        )
        await _seed_cell(case_id=case_id, run_id=run_id, status="pass")

        async with AsyncSessionLocal() as db:
            from tests.test_queue import _identity
            result = await stp_matrix_svc.publish_stp_matrix(
                db, _identity(department_id=dept_a),
                department_id=dept_a, os_version_id=os_version_id,
            )

        assert result.status == StpMatrixPublicationStatus.POSTED
        assert calls["create"] == [
            "Состав тестового прогона", "STRESS_stp ⬝ 1.8.6", "1.8.6.38",
        ]
        assert "1.8.6.38" in result.body_snapshot
        assert os_version_id not in result.body_snapshot
        assert all(os_version_id not in title for title in calls["create"] + calls["find"])

    async def test_unresolvable_catalog_id_fails_instead_of_publishing_garbage(
        self, dept_a, mock_secret_client, mock_confluence_pages, monkeypatch,
    ):
        calls, _pages = mock_confluence_pages
        mock_secret_client["cred_x"] = ("bot", "tok123")
        await _seed_integration_settings(dept_a)
        stand_id = await _seed_stand(dept_a)
        case_id = await _seed_case("pg", "PostgreSQL")
        run_id = await _seed_run(
            os_version_id="osv_deadbeef", mode="orel", kernel="6.1.0", stand_id=stand_id,
        )
        await _seed_cell(case_id=case_id, run_id=run_id, status="pass")

        async def boom(os_version_id: str) -> dict:
            from src.core.exceptions import NotFoundError

            raise NotFoundError(
                error_code="SERVER_SERVICE_OBJECT_NOT_FOUND", message="no such os_version",
            )

        monkeypatch.setattr(server_client, "get_os_version", boom)

        async with AsyncSessionLocal() as db:
            from tests.test_queue import _identity
            result = await stp_matrix_svc.publish_stp_matrix(
                db, _identity(department_id=dept_a),
                department_id=dept_a, os_version_id="osv_deadbeef",
            )

        assert result.status == StpMatrixPublicationStatus.FAILED
        assert calls["create"] == []

    async def test_second_publish_with_same_body_is_a_noop(
        self, dept_a, mock_secret_client, mock_confluence_pages,
    ):
        calls, _pages = mock_confluence_pages
        mock_secret_client["cred_x"] = ("bot", "tok123")
        await _seed_integration_settings(dept_a)
        stand_id = await _seed_stand(dept_a)
        case_id = await _seed_case("pg", "PostgreSQL")
        run_id = await _seed_run(os_version_id="1.8.5.46", mode="orel", kernel="6.1.0", stand_id=stand_id)
        await _seed_cell(case_id=case_id, run_id=run_id, status="pass")

        from tests.test_queue import _identity
        identity = _identity(department_id=dept_a)

        async with AsyncSessionLocal() as db:
            first = await stp_matrix_svc.publish_stp_matrix(
                db, identity, department_id=dept_a, os_version_id="1.8.5.46",
            )
        assert first.status == StpMatrixPublicationStatus.POSTED
        assert len(calls["create"]) == 3

        async with AsyncSessionLocal() as db:
            second = await stp_matrix_svc.publish_stp_matrix(
                db, identity, department_id=dept_a, os_version_id="1.8.5.46",
            )
        assert second.status == StpMatrixPublicationStatus.POSTED
        assert second.confluence_page_id == first.confluence_page_id
        # Тело не изменилось — ни create, ни update, ни даже find не вызывались повторно.
        assert len(calls["create"]) == 3
        assert len(calls["update"]) == 0

    async def test_changed_body_triggers_update_not_create(
        self, dept_a, mock_secret_client, mock_confluence_pages,
    ):
        calls, _pages = mock_confluence_pages
        mock_secret_client["cred_x"] = ("bot", "tok123")
        await _seed_integration_settings(dept_a)
        stand_id = await _seed_stand(dept_a)
        case_id = await _seed_case("pg", "PostgreSQL")
        run_id = await _seed_run(os_version_id="1.8.5.46", mode="orel", kernel="6.1.0", stand_id=stand_id)
        await _seed_cell(case_id=case_id, run_id=run_id, status="not_run")

        from tests.test_queue import _identity
        identity = _identity(department_id=dept_a)

        async with AsyncSessionLocal() as db:
            first = await stp_matrix_svc.publish_stp_matrix(
                db, identity, department_id=dept_a, os_version_id="1.8.5.46",
            )
        assert len(calls["create"]) == 3

        # Статус ячейки поменялся — тело страницы теперь другое.
        async with AsyncSessionLocal() as db:
            cell = await stp_cell_repo.get_by_case_and_run(db, stp_test_case_id=case_id, stp_test_run_id=run_id)
            await stp_cell_repo.update(db, cell, {"status": "pass"})
            await db.commit()

        async with AsyncSessionLocal() as db:
            second = await stp_matrix_svc.publish_stp_matrix(
                db, identity, department_id=dept_a, os_version_id="1.8.5.46",
            )
        assert second.status == StpMatrixPublicationStatus.POSTED
        assert second.confluence_page_id == first.confluence_page_id
        assert len(calls["create"]) == 3  # без новых create
        assert len(calls["update"]) == 1
        assert calls["update"][0][0] == first.confluence_page_id

    async def test_other_department_stands_are_excluded(
        self, dept_a, mock_secret_client, mock_confluence_pages,
    ):
        """Стенды/прогоны чужого отдела не попадают в матрицу этого отдела."""
        calls, _pages = mock_confluence_pages
        mock_secret_client["cred_x"] = ("bot", "tok123")
        await _seed_integration_settings(dept_a)
        other_stand_id = await _seed_stand("dep_other")
        await _seed_run(os_version_id="1.8.5.46", mode="orel", kernel="6.1.0", stand_id=other_stand_id)

        async with AsyncSessionLocal() as db:
            from tests.test_queue import _identity
            result = await stp_matrix_svc.publish_stp_matrix(
                db, _identity(department_id=dept_a),
                department_id=dept_a, os_version_id="1.8.5.46",
            )
        assert result.status == StpMatrixPublicationStatus.SKIPPED_NO_TEST_RUNS
        assert calls["create"] == []


# ── POST /stp/matrix/publish ─────────────────────────────────────────────────


class TestPublishEndpoint:
    async def test_requires_auth(self, client):
        resp = await client.post(MATRIX_BASE, json={"os_version_id": "1.8.5.46"})
        assert resp.status_code == 401

    async def test_guest_cannot_publish(self, client, guest_token):
        resp = await client.post(
            MATRIX_BASE, headers=_hdr(guest_token), json={"os_version_id": "1.8.5.46"},
        )
        assert resp.status_code == 403

    async def test_admin_gets_skip_status_when_not_configured(self, client, admin_token):
        resp = await client.post(
            MATRIX_BASE, headers=_hdr(admin_token), json={"os_version_id": "1.8.5.99"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "skipped_not_configured"

    async def test_cross_department_is_rejected(self, client, admin_token):
        resp = await client.post(
            MATRIX_BASE, headers=_hdr(admin_token),
            json={"os_version_id": "1.8.5.46", "department_id": "dep_other_xyz"},
        )
        assert resp.status_code == 403

    async def test_full_publish_via_endpoint(
        self, client, admin_token, dept_a, mock_secret_client, mock_confluence_pages,
    ):
        mock_secret_client["cred_x"] = ("bot", "tok123")
        await _seed_integration_settings(dept_a)
        stand_id = await _seed_stand(dept_a)
        case_id = await _seed_case("pg", "PostgreSQL")
        run_id = await _seed_run(os_version_id="1.8.5.46", mode="orel", kernel="6.1.0", stand_id=stand_id)
        await _seed_cell(case_id=case_id, run_id=run_id, status="pass")

        resp = await client.post(
            MATRIX_BASE, headers=_hdr(admin_token), json={"os_version_id": "1.8.5.46"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "posted"
        assert body["department_id"] == dept_a
        assert body["confluence_page_id"] is not None


# ── department_integration_settings: новые поля ─────────────────────────────


class TestIntegrationSettingsStpMatrixFields:
    async def test_upsert_roundtrip(self, client, admin_token, dept_a):
        put_resp = await client.put(
            f"{DIS_BASE}/{dept_a}", headers=_hdr(admin_token),
            json={
                "stp_matrix_confluence_space": "DEPTQA",
                "stp_matrix_confluence_root_page_title": "Состав тестового прогона",
            },
        )
        assert put_resp.status_code == 200, put_resp.text
        assert put_resp.json()["stp_matrix_confluence_space"] == "DEPTQA"
        assert put_resp.json()["stp_matrix_confluence_root_page_title"] == "Состав тестового прогона"

        get_resp = await client.get(f"{DIS_BASE}/{dept_a}", headers=_hdr(admin_token))
        assert get_resp.json()["stp_matrix_confluence_space"] == "DEPTQA"

    async def test_default_is_null(self, client, guest_token, dept_a):
        resp = await client.get(f"{DIS_BASE}/{dept_a}", headers=_hdr(guest_token))
        assert resp.json()["stp_matrix_confluence_space"] is None
        assert resp.json()["stp_matrix_confluence_root_page_title"] is None
