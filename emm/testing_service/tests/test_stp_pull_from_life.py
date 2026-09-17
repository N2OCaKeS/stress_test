"""Тесты «Pull СТП из life» (§D8 плана миграции).

Переиспользует стенды/каталог/моки Jira из `tests.test_stp` — тот же стиль,
что `test_stp_add_test.py`. `zephyr_client.search_test_runs`/`get_test_run`
мокаются отдельно (`mock_zephyr_pull`) — эта волна только ЧИТАЕТ Zephyr,
`mock_zephyr`/`mock_create_test_case` из `test_stp.py` здесь не нужны.
"""

from __future__ import annotations

import uuid

import pytest

from src.core.constants import StpCellStatus
from src.db.session import AsyncSessionLocal
from src.repositories import stp_cell as stp_cell_repo
from src.repositories import stp_test_case as stp_test_case_repo
from src.repositories import stp_test_run as stp_test_run_repo
from src.services import stp_pull_from_life as pull_svc
from src.services import zephyr_client
from src.services.zephyr_client import ZephyrTestRunDetail, ZephyrTestRunResultItem, ZephyrTestRunSummary
from src.utils.ids import stp_cell_id as new_cell_id
from src.utils.ids import stp_test_case_id as new_case_id
from tests.conftest import auth_hdr as _hdr
from tests.test_queue import _create_stand, mock_server_service, recorded_calls  # noqa: F401
from tests.test_stp import STP_BASE, _seed_integration_settings, mock_secret_client  # noqa: F401

PULL_BASE = STP_BASE + "/pull-from-life"


@pytest.fixture
def mock_zephyr_pull(monkeypatch):
    """`{run_key: ZephyrTestRunDetail}` — что вернёт `get_test_run` на этот ключ.
    `search_test_runs` всегда отдаёт ровно те summary, что положили в `runs`."""
    state = {"runs": [], "details": {}}

    async def fake_search(*, base_url, bearer_token, folder, project_key="BT"):
        return list(state["runs"])

    async def fake_get_test_run(*, base_url, bearer_token, test_run_key):
        return state["details"][test_run_key]

    monkeypatch.setattr(zephyr_client, "search_test_runs", fake_search)
    monkeypatch.setattr(zephyr_client, "get_test_run", fake_get_test_run)
    return state


def _run_summary(name: str, key: str = "BT-R1") -> ZephyrTestRunSummary:
    return ZephyrTestRunSummary(key=key, name=name, folder="/stress_test/1.9/1.9.0.1")


def _run_detail(key: str, items: list[tuple[str, str]], name: str = "") -> ZephyrTestRunDetail:
    return ZephyrTestRunDetail(
        key=key, name=name, folder=None,
        items=[ZephyrTestRunResultItem(test_case_key=k, status=s, test_case_name=k) for k, s in items],
    )


class TestDeriveRelease:
    """Поиск обязан ходить в легаси-папку (`parts[:3]`, для UU — `parts[:5]`),
    иначе легаси-раны того же РЦ не находятся вообще."""

    def test_ordinary_four_segment_release_keeps_three(self):
        assert pull_svc._derive_release("1.8.5.46") == "1.8.5"

    def test_search_folder_matches_legacy_example(self):
        rc = "1.8.5.46"
        assert f"/stress_test/{pull_svc._derive_release(rc)}/{rc}" == "/stress_test/1.8.5/1.8.5.46"

    def test_uu_hotfix_keeps_five_segments(self):
        assert pull_svc._derive_release("1.7.3.UU.1.2") == "1.7.3.UU.1"

    def test_short_format_falls_back_without_raising(self):
        assert pull_svc._derive_release("1.8") == "1.8"


class TestParseRunName:
    def test_legacy_style_name_without_underscore_in_stand(self):
        assert pull_svc.parse_run_name("1.8.5.46_orel_6.1.0_stand1") == ("1.8.5.46", "orel", "6.1.0", "stand1")

    def test_emm_style_stand_id_keeps_its_own_underscore_intact(self):
        # stand_<hex> несёт собственное подчёркивание — наивный split('_') дал
        # бы 5 токенов вместо 4; split('_', 3) должен оставить его целым.
        parsed = pull_svc.parse_run_name("1.9.0.1_orel_6.1.0_stand_abc123")
        assert parsed == ("1.9.0.1", "orel", "6.1.0", "stand_abc123")

    def test_too_few_tokens_is_unparseable(self):
        assert pull_svc.parse_run_name("only_two_tokens") is None

    def test_empty_token_is_unparseable(self):
        assert pull_svc.parse_run_name("1.9.0.1__6.1.0_stand1") is None

    def test_empty_name_is_unparseable(self):
        assert pull_svc.parse_run_name("") is None


class TestPreview:
    async def test_parses_and_matches_stand_new_run(
        self, client, admin_token, mock_server_service, mock_secret_client, mock_zephyr_pull, dept_a,
    ):
        mock_server_service()
        stand_id, _ = await _create_stand(client, admin_token, department_id=dept_a)
        await _seed_integration_settings(dept_a)
        mock_secret_client["cred_x"] = ("jira_bot", "tok123")
        rc = f"1.9.0.{uuid.uuid4().hex[:6]}"
        name = f"{rc}_orel_6.1.0_{stand_id}"
        mock_zephyr_pull["runs"] = [_run_summary(name)]
        mock_zephyr_pull["details"]["BT-R1"] = _run_detail("BT-R1", [("BT-T1", "pass"), ("BT-T2", "not_run")])

        resp = await client.post(
            f"{PULL_BASE}/preview", headers=_hdr(admin_token),
            json={"os_version_id": rc, "department_id": dept_a},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total_found"] == 1
        assert body["new_count"] == 1
        assert body["already_imported_count"] == 0
        assert body["needs_manual_mapping_count"] == 0
        item = body["items"][0]
        assert item["zephyr_key"] == "BT-R1"
        assert item["parsed_stand_token"] == stand_id
        assert item["stand_id"] == stand_id
        assert item["needs_manual_mapping"] is False
        assert item["already_imported"] is False
        assert item["composition"]["case_count"] == 2
        assert item["composition"]["new_case_count"] == 2

    async def test_unparseable_name_needs_manual_mapping(
        self, client, admin_token, mock_server_service, mock_secret_client, mock_zephyr_pull, dept_a,
    ):
        mock_server_service()
        await _seed_integration_settings(dept_a)
        mock_secret_client["cred_x"] = ("jira_bot", "tok123")
        rc = f"1.9.0.{uuid.uuid4().hex[:6]}"
        mock_zephyr_pull["runs"] = [_run_summary("weird-name-not-our-convention", key="BT-R2")]
        mock_zephyr_pull["details"]["BT-R2"] = _run_detail("BT-R2", [])

        resp = await client.post(
            f"{PULL_BASE}/preview", headers=_hdr(admin_token),
            json={"os_version_id": rc, "department_id": dept_a},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["needs_manual_mapping_count"] == 1
        assert body["items"][0]["mapping_issue"] == "NAME_NOT_PARSEABLE"

    async def test_unresolvable_stand_needs_manual_mapping(
        self, client, admin_token, mock_server_service, mock_secret_client, mock_zephyr_pull, dept_a,
    ):
        mock_server_service()
        await _seed_integration_settings(dept_a)
        mock_secret_client["cred_x"] = ("jira_bot", "tok123")
        rc = f"1.9.0.{uuid.uuid4().hex[:6]}"
        # Токен "stand1" — легаси-стиль, не совпадает ни с одним EMM test_stands.id.
        mock_zephyr_pull["runs"] = [_run_summary(f"{rc}_orel_6.1.0_stand1", key="BT-R3")]
        mock_zephyr_pull["details"]["BT-R3"] = _run_detail("BT-R3", [])

        resp = await client.post(
            f"{PULL_BASE}/preview", headers=_hdr(admin_token),
            json={"os_version_id": rc, "department_id": dept_a},
        )
        assert resp.status_code == 200, resp.text
        item = resp.json()["items"][0]
        assert item["needs_manual_mapping"] is True
        assert item["mapping_issue"] == "STAND_NOT_FOUND"
        assert item["stand_id"] is None

    async def test_already_imported_run_is_flagged(
        self, client, admin_token, mock_server_service, mock_secret_client, mock_zephyr_pull, dept_a,
    ):
        mock_server_service()
        stand_id, _ = await _create_stand(client, admin_token, department_id=dept_a)
        await _seed_integration_settings(dept_a)
        mock_secret_client["cred_x"] = ("jira_bot", "tok123")
        rc = f"1.9.0.{uuid.uuid4().hex[:6]}"
        name = f"{rc}_orel_6.1.0_{stand_id}"
        async with AsyncSessionLocal() as db:
            run = await stp_test_run_repo.create(db, {
                "id": "stpr_existing", "os_version_id": rc, "mode": "orel", "kernel": "6.1.0",
                "stand_id": stand_id, "zephyr_test_run_key": "BT-R1", "zephyr_folder_path": "/x",
            })
            await db.commit()
            run_id = run.id
        mock_zephyr_pull["runs"] = [_run_summary(name)]
        mock_zephyr_pull["details"]["BT-R1"] = _run_detail("BT-R1", [])

        resp = await client.post(
            f"{PULL_BASE}/preview", headers=_hdr(admin_token),
            json={"os_version_id": rc, "department_id": dept_a},
        )
        assert resp.status_code == 200, resp.text
        item = resp.json()["items"][0]
        assert item["already_imported"] is True
        assert item["stp_test_run_id"] == run_id

    async def test_preview_makes_no_db_writes(
        self, client, admin_token, mock_server_service, mock_secret_client, mock_zephyr_pull, dept_a,
    ):
        mock_server_service()
        stand_id, _ = await _create_stand(client, admin_token, department_id=dept_a)
        await _seed_integration_settings(dept_a)
        mock_secret_client["cred_x"] = ("jira_bot", "tok123")
        rc = f"1.9.0.{uuid.uuid4().hex[:6]}"
        name = f"{rc}_orel_6.1.0_{stand_id}"
        mock_zephyr_pull["runs"] = [_run_summary(name)]
        mock_zephyr_pull["details"]["BT-R1"] = _run_detail("BT-R1", [("BT-T1", "pass")])

        resp = await client.post(
            f"{PULL_BASE}/preview", headers=_hdr(admin_token),
            json={"os_version_id": rc, "department_id": dept_a},
        )
        assert resp.status_code == 200, resp.text

        async with AsyncSessionLocal() as db:
            runs = await stp_test_run_repo.list_all(db, limit=500, offset=0)
        assert runs == []
        async with AsyncSessionLocal() as db:
            case = await stp_test_case_repo.get_by_zephyr_id(db, "BT-T1")
        assert case is None

    async def test_guest_forbidden(self, client, guest_token, mock_server_service, dept_a):
        mock_server_service()
        resp = await client.post(
            f"{PULL_BASE}/preview", headers=_hdr(guest_token),
            json={"os_version_id": "1.9.0.1", "department_id": dept_a},
        )
        assert resp.status_code == 403

    async def test_integration_not_configured_is_503(self, client, admin_token, mock_server_service, dept_a):
        mock_server_service()
        resp = await client.post(
            f"{PULL_BASE}/preview", headers=_hdr(admin_token),
            json={"os_version_id": "1.9.0.1", "department_id": dept_a},
        )
        assert resp.status_code == 503
        assert resp.json()["error_code"] == "JIRA_INTEGRATION_NOT_AVAILABLE"


class TestImport:
    async def test_creates_run_case_and_cell(
        self, client, admin_token, mock_server_service, mock_secret_client, mock_zephyr_pull, dept_a,
    ):
        mock_server_service()
        stand_id, _ = await _create_stand(client, admin_token, department_id=dept_a)
        await _seed_integration_settings(dept_a)
        mock_secret_client["cred_x"] = ("jira_bot", "tok123")
        rc = f"1.9.0.{uuid.uuid4().hex[:6]}"
        name = f"{rc}_orel_6.1.0_{stand_id}"
        mock_zephyr_pull["runs"] = [_run_summary(name)]
        mock_zephyr_pull["details"]["BT-R1"] = _run_detail("BT-R1", [("BT-T1", "pass")])

        resp = await client.post(
            f"{PULL_BASE}/import", headers=_hdr(admin_token),
            json={"os_version_id": rc, "department_id": dept_a},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["created_runs"] == 1
        assert body["matched_runs"] == 0
        assert body["cases_created"] == 1
        assert body["cells_created"] == 1
        assert body["conflicts_count"] == 0
        result = body["results"][0]
        assert result["status"] == "succeeded"

        async with AsyncSessionLocal() as db:
            run = await stp_test_run_repo.get_by_zephyr_key(db, "BT-R1")
            case = await stp_test_case_repo.get_by_zephyr_id(db, "BT-T1")
            cell = await stp_cell_repo.get_by_case_and_run(db, stp_test_case_id=case.id, stp_test_run_id=run.id)
        assert run is not None and run.stand_id == stand_id
        assert case is not None
        assert cell is not None and cell.status == StpCellStatus.PASSED

    async def test_import_twice_does_not_duplicate(
        self, client, admin_token, mock_server_service, mock_secret_client, mock_zephyr_pull, dept_a,
    ):
        mock_server_service()
        stand_id, _ = await _create_stand(client, admin_token, department_id=dept_a)
        await _seed_integration_settings(dept_a)
        mock_secret_client["cred_x"] = ("jira_bot", "tok123")
        rc = f"1.9.0.{uuid.uuid4().hex[:6]}"
        name = f"{rc}_orel_6.1.0_{stand_id}"
        mock_zephyr_pull["runs"] = [_run_summary(name)]
        mock_zephyr_pull["details"]["BT-R1"] = _run_detail("BT-R1", [("BT-T1", "pass")])

        first = await client.post(
            f"{PULL_BASE}/import", headers=_hdr(admin_token), json={"os_version_id": rc, "department_id": dept_a},
        )
        assert first.status_code == 200, first.text
        second = await client.post(
            f"{PULL_BASE}/import", headers=_hdr(admin_token), json={"os_version_id": rc, "department_id": dept_a},
        )
        assert second.status_code == 200, second.text
        body = second.json()
        assert body["created_runs"] == 0
        assert body["matched_runs"] == 1
        assert body["cases_created"] == 0
        assert body["cases_matched"] == 1
        assert body["cells_created"] == 0
        assert body["cells_matched"] == 1

        async with AsyncSessionLocal() as db:
            run = await stp_test_run_repo.get_by_zephyr_key(db, "BT-R1")
            cells = await stp_cell_repo.list_by_run(db, run.id)
        assert len(cells) == 1

    async def test_diverging_local_status_is_a_conflict_not_overwritten(
        self, client, admin_token, mock_server_service, mock_secret_client, mock_zephyr_pull, dept_a,
    ):
        mock_server_service()
        stand_id, _ = await _create_stand(client, admin_token, department_id=dept_a)
        await _seed_integration_settings(dept_a)
        mock_secret_client["cred_x"] = ("jira_bot", "tok123")
        rc = f"1.9.0.{uuid.uuid4().hex[:6]}"
        name = f"{rc}_orel_6.1.0_{stand_id}"

        # Локальный прогон/тест-кейс/ячейка уже существуют — с исходом,
        # который РАСХОДИТСЯ с тем, что "говорит" Zephyr.
        async with AsyncSessionLocal() as db:
            run = await stp_test_run_repo.create(db, {
                "id": "stpr_conflict", "os_version_id": rc, "mode": "orel", "kernel": "6.1.0",
                "stand_id": stand_id, "zephyr_test_run_key": "BT-R1", "zephyr_folder_path": "/x",
            })
            case = await stp_test_case_repo.create(db, {
                "id": new_case_id(), "code": "local.code", "title": "Local", "zephyr_id": "BT-T1",
                "department_id": dept_a, "created_by": "usr_test",
            })
            await db.flush()
            cell = await stp_cell_repo.create(db, {
                "id": new_cell_id(), "stp_test_case_id": case.id, "stp_test_run_id": run.id,
                "status": StpCellStatus.FAIL, "is_active": True, "updated_by": "usr_qa",
            })
            await db.commit()
            cell_id = cell.id

        mock_zephyr_pull["runs"] = [_run_summary(name)]
        mock_zephyr_pull["details"]["BT-R1"] = _run_detail("BT-R1", [("BT-T1", "pass")])

        resp = await client.post(
            f"{PULL_BASE}/import", headers=_hdr(admin_token), json={"os_version_id": rc, "department_id": dept_a},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["conflicts_count"] == 1
        result = body["results"][0]
        assert len(result["conflicts"]) == 1
        conflict = result["conflicts"][0]
        assert conflict["stp_cell_id"] == cell_id
        assert conflict["local_status"] == StpCellStatus.FAIL
        assert conflict["zephyr_status"] == StpCellStatus.PASSED

        async with AsyncSessionLocal() as db:
            reloaded = await stp_cell_repo.get_by_id(db, cell_id)
        assert reloaded.status == StpCellStatus.FAIL  # не перезаписано

    async def test_matching_local_status_is_not_a_conflict(
        self, client, admin_token, mock_server_service, mock_secret_client, mock_zephyr_pull, dept_a,
    ):
        mock_server_service()
        stand_id, _ = await _create_stand(client, admin_token, department_id=dept_a)
        await _seed_integration_settings(dept_a)
        mock_secret_client["cred_x"] = ("jira_bot", "tok123")
        rc = f"1.9.0.{uuid.uuid4().hex[:6]}"
        name = f"{rc}_orel_6.1.0_{stand_id}"

        async with AsyncSessionLocal() as db:
            run = await stp_test_run_repo.create(db, {
                "id": "stpr_match", "os_version_id": rc, "mode": "orel", "kernel": "6.1.0",
                "stand_id": stand_id, "zephyr_test_run_key": "BT-R1", "zephyr_folder_path": "/x",
            })
            case = await stp_test_case_repo.create(db, {
                "id": new_case_id(), "code": "local.code2", "title": "Local", "zephyr_id": "BT-T1",
                "department_id": dept_a, "created_by": "usr_test",
            })
            await db.flush()
            await stp_cell_repo.create(db, {
                "id": new_cell_id(), "stp_test_case_id": case.id, "stp_test_run_id": run.id,
                "status": StpCellStatus.PASSED, "is_active": True, "updated_by": "usr_qa",
            })
            await db.commit()

        mock_zephyr_pull["runs"] = [_run_summary(name)]
        mock_zephyr_pull["details"]["BT-R1"] = _run_detail("BT-R1", [("BT-T1", "pass")])

        resp = await client.post(
            f"{PULL_BASE}/import", headers=_hdr(admin_token), json={"os_version_id": rc, "department_id": dept_a},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["conflicts_count"] == 0
        assert body["cells_matched"] == 1
        assert body["cells_created"] == 0

    async def test_unparseable_name_is_skipped_not_crashed(
        self, client, admin_token, mock_server_service, mock_secret_client, mock_zephyr_pull, dept_a,
    ):
        mock_server_service()
        await _seed_integration_settings(dept_a)
        mock_secret_client["cred_x"] = ("jira_bot", "tok123")
        rc = f"1.9.0.{uuid.uuid4().hex[:6]}"
        mock_zephyr_pull["runs"] = [_run_summary("weird-name", key="BT-R4")]
        mock_zephyr_pull["details"]["BT-R4"] = _run_detail("BT-R4", [])

        resp = await client.post(
            f"{PULL_BASE}/import", headers=_hdr(admin_token), json={"os_version_id": rc, "department_id": dept_a},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["skipped_count"] == 1
        assert body["results"][0]["status"] == "skipped_needs_manual_mapping"

    async def test_selective_import_by_zephyr_keys(
        self, client, admin_token, mock_server_service, mock_secret_client, mock_zephyr_pull, dept_a,
    ):
        mock_server_service()
        stand_id, _ = await _create_stand(client, admin_token, department_id=dept_a)
        await _seed_integration_settings(dept_a)
        mock_secret_client["cred_x"] = ("jira_bot", "tok123")
        rc = f"1.9.0.{uuid.uuid4().hex[:6]}"
        name1 = f"{rc}_orel_6.1.0_{stand_id}"
        name2 = f"{rc}_smolensk_6.1.0_{stand_id}"
        mock_zephyr_pull["runs"] = [_run_summary(name1, key="BT-R1"), _run_summary(name2, key="BT-R5")]
        mock_zephyr_pull["details"]["BT-R1"] = _run_detail("BT-R1", [])
        mock_zephyr_pull["details"]["BT-R5"] = _run_detail("BT-R5", [])

        resp = await client.post(
            f"{PULL_BASE}/import", headers=_hdr(admin_token),
            json={"os_version_id": rc, "department_id": dept_a, "zephyr_keys": ["BT-R1"]},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert len(body["results"]) == 1
        assert body["results"][0]["zephyr_key"] == "BT-R1"

    async def test_guest_forbidden(self, client, guest_token, mock_server_service, dept_a):
        mock_server_service()
        resp = await client.post(
            f"{PULL_BASE}/import", headers=_hdr(guest_token),
            json={"os_version_id": "1.9.0.1", "department_id": dept_a},
        )
        assert resp.status_code == 403
