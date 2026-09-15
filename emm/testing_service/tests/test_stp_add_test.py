"""Тесты добавления одного теста EMM в СТП (§D6/D7 плана миграции).

Переиспользует стенды/тест-каталог/моки Jira из `tests.test_stp` — тот же
стиль, что `test_stp_composition.py`. Публикация СТП-матрицы (шаг 4) не
мокается отдельно для happy-path: `department_integration_settings` в этих
тестах не несёт Confluence-настроек, поэтому `stp_matrix.publish_stp_matrix`
естественно возвращает `skipped_not_configured` — легитимный терминальный
исход шага, не провал. Провал шага 4 отдельно смоделирован через прямой
monkeypatch `stp_matrix.publish_stp_matrix`, не через Confluence-моки (те уже
покрыты `test_stp_matrix.py`).
"""

from __future__ import annotations

import uuid

import pytest

from src.core.constants import StpMatrixPublicationStatus
from src.db.session import AsyncSessionLocal
from src.repositories import stp_cell as stp_cell_repo
from src.repositories import stp_test_case as stp_test_case_repo
from src.repositories import stp_test_run as stp_test_run_repo
from src.services import stp_matrix, zephyr_client
from src.utils.ids import stp_test_run_id as new_run_id
from tests.conftest import auth_hdr as _hdr
from tests.test_queue import _create_stand, mock_server_service, recorded_calls  # noqa: F401
from tests.test_stp import (
    STP_BASE,
    _create_test_def_for_dept,
    _seed_integration_settings,
    _seed_stp_test_case,
    mock_secret_client,  # noqa: F401
    mock_zephyr,  # noqa: F401
)

ADD_TEST_BASE = STP_BASE + "/test-runs"


@pytest.fixture
def mock_create_test_case(monkeypatch):
    calls: list[dict] = []

    async def fake_create_test_case(*, base_url, bearer_token, name, folder, project_key="BT", owner_key=None):
        calls.append({"base_url": base_url, "bearer_token": bearer_token, "name": name, "folder": folder, "owner_key": owner_key})
        return f"BT-T{len(calls)}"

    monkeypatch.setattr(zephyr_client, "create_test_case", fake_create_test_case)
    return calls


async def _seed_run(*, department_id: str, stand_id: str, os_version_id: str, zephyr_test_run_key="BT-R1") -> str:
    async with AsyncSessionLocal() as db:
        run = await stp_test_run_repo.create(db, {
            "id": new_run_id(), "os_version_id": os_version_id, "mode": "orel", "kernel": "6.1.0",
            "stand_id": stand_id, "zephyr_test_run_key": zephyr_test_run_key,
            "zephyr_folder_path": "/stress_test/x",
        })
        await db.commit()
        return run.id


class TestAddTestToStp:
    async def test_creates_missing_testcase_and_cell_and_succeeds(
        self, client, admin_token, mock_server_service, mock_zephyr, mock_create_test_case,
        mock_secret_client, dept_a,
    ):
        mock_server_service()
        stand_id, _ = await _create_stand(client, admin_token, department_id=dept_a)
        test_id, code = await _create_test_def_for_dept(client, admin_token, stand_id, dept_a)
        await _seed_integration_settings(dept_a)
        mock_secret_client["cred_x"] = ("jira_bot", "tok123")
        rc = f"1.9.0.{uuid.uuid4().hex[:6]}"
        run_id = await _seed_run(department_id=dept_a, stand_id=stand_id, os_version_id=rc)

        resp = await client.post(
            f"{ADD_TEST_BASE}/{run_id}/add-test", headers=_hdr(admin_token), json={"test_id": test_id},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "succeeded"
        assert body["zephyr_testcase_created"] is True
        assert body["zephyr_added_to_run"] is True
        assert body["stp_cell_created"] is True
        assert body["life_published"] is True
        assert len(mock_create_test_case) == 1
        assert len(mock_zephyr["add"]) == 1

        async with AsyncSessionLocal() as db:
            case = await stp_test_case_repo.get_by_code(db, code)
            cell = await stp_cell_repo.get_by_case_and_run(db, stp_test_case_id=case.id, stp_test_run_id=run_id)
        assert case.zephyr_id == "BT-T1"
        assert cell is not None

    async def test_reuses_existing_linked_testcase_without_duplicate_zephyr_call(
        self, client, admin_token, mock_server_service, mock_zephyr, mock_create_test_case,
        mock_secret_client, dept_a,
    ):
        mock_server_service()
        stand_id, _ = await _create_stand(client, admin_token, department_id=dept_a)
        test_id, code = await _create_test_def_for_dept(client, admin_token, stand_id, dept_a)
        await _seed_stp_test_case(code, zephyr_id="BT-T-EXISTING")
        await _seed_integration_settings(dept_a)
        mock_secret_client["cred_x"] = ("jira_bot", "tok123")
        rc = f"1.9.0.{uuid.uuid4().hex[:6]}"
        run_id = await _seed_run(department_id=dept_a, stand_id=stand_id, os_version_id=rc)

        resp = await client.post(
            f"{ADD_TEST_BASE}/{run_id}/add-test", headers=_hdr(admin_token), json={"test_id": test_id},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "succeeded"
        assert len(mock_create_test_case) == 0  # уже была связь — Zephyr testcase не заводили заново
        assert len(mock_zephyr["add"]) == 1

    async def test_repeat_call_after_success_is_a_noop(
        self, client, admin_token, mock_server_service, mock_zephyr, mock_create_test_case,
        mock_secret_client, dept_a,
    ):
        mock_server_service()
        stand_id, _ = await _create_stand(client, admin_token, department_id=dept_a)
        test_id, _code = await _create_test_def_for_dept(client, admin_token, stand_id, dept_a)
        await _seed_integration_settings(dept_a)
        mock_secret_client["cred_x"] = ("jira_bot", "tok123")
        rc = f"1.9.0.{uuid.uuid4().hex[:6]}"
        run_id = await _seed_run(department_id=dept_a, stand_id=stand_id, os_version_id=rc)

        first = await client.post(
            f"{ADD_TEST_BASE}/{run_id}/add-test", headers=_hdr(admin_token), json={"test_id": test_id},
        )
        assert first.status_code == 200, first.text
        op_id = first.json()["id"]

        second = await client.post(
            f"{ADD_TEST_BASE}/{run_id}/add-test", headers=_hdr(admin_token), json={"test_id": test_id},
        )
        assert second.status_code == 200, second.text
        assert second.json()["id"] == op_id
        assert second.json()["status"] == "succeeded"
        assert len(mock_create_test_case) == 1  # второй вызов ничего не создавал повторно
        assert len(mock_zephyr["add"]) == 1

    async def test_life_publish_failure_keeps_zephyr_steps_and_marks_failed(
        self, client, admin_token, mock_server_service, mock_zephyr, mock_create_test_case,
        mock_secret_client, dept_a, monkeypatch,
    ):
        mock_server_service()
        stand_id, _ = await _create_stand(client, admin_token, department_id=dept_a)
        test_id, _code = await _create_test_def_for_dept(client, admin_token, stand_id, dept_a)
        await _seed_integration_settings(dept_a)
        mock_secret_client["cred_x"] = ("jira_bot", "tok123")
        rc = f"1.9.0.{uuid.uuid4().hex[:6]}"
        run_id = await _seed_run(department_id=dept_a, stand_id=stand_id, os_version_id=rc)

        class _FakePublication:
            status = StpMatrixPublicationStatus.FAILED
            error = "confluence unreachable"

        async def failing_publish(db, identity, *, department_id, os_version_id):
            return _FakePublication()

        original_publish = stp_matrix.publish_stp_matrix
        monkeypatch.setattr(stp_matrix, "publish_stp_matrix", failing_publish)

        resp = await client.post(
            f"{ADD_TEST_BASE}/{run_id}/add-test", headers=_hdr(admin_token), json={"test_id": test_id},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "failed"
        assert body["zephyr_testcase_created"] is True
        assert body["zephyr_added_to_run"] is True
        assert body["stp_cell_created"] is True
        assert body["life_published"] is False
        assert "confluence unreachable" in body["last_error"]

        # Повтор с рабочей публикацией продолжает ровно с шага 4, не трогая Zephyr снова.
        monkeypatch.setattr(stp_matrix, "publish_stp_matrix", original_publish)
        second = await client.post(
            f"{ADD_TEST_BASE}/{run_id}/add-test", headers=_hdr(admin_token), json={"test_id": test_id},
        )
        assert second.status_code == 200, second.text
        assert second.json()["status"] == "succeeded"
        assert second.json()["life_published"] is True
        assert len(mock_create_test_case) == 1
        assert len(mock_zephyr["add"]) == 1

    async def test_missing_test_definition_is_404(self, client, admin_token, mock_server_service, dept_a):
        mock_server_service()
        stand_id, _ = await _create_stand(client, admin_token, department_id=dept_a)
        rc = f"1.9.0.{uuid.uuid4().hex[:6]}"
        run_id = await _seed_run(department_id=dept_a, stand_id=stand_id, os_version_id=rc)
        resp = await client.post(
            f"{ADD_TEST_BASE}/{run_id}/add-test", headers=_hdr(admin_token), json={"test_id": "tdef_does_not_exist"},
        )
        assert resp.status_code == 404

    async def test_missing_run_is_404(self, client, admin_token, mock_server_service, dept_a):
        mock_server_service()
        stand_id, _ = await _create_stand(client, admin_token, department_id=dept_a)
        test_id, _code = await _create_test_def_for_dept(client, admin_token, stand_id, dept_a)
        resp = await client.post(
            f"{ADD_TEST_BASE}/stprun_does_not_exist/add-test", headers=_hdr(admin_token), json={"test_id": test_id},
        )
        assert resp.status_code == 404

    async def test_guest_forbidden(self, client, guest_token, admin_token, mock_server_service, dept_a):
        mock_server_service()
        stand_id, _ = await _create_stand(client, admin_token, department_id=dept_a)
        test_id, _code = await _create_test_def_for_dept(client, admin_token, stand_id, dept_a)
        rc = f"1.9.0.{uuid.uuid4().hex[:6]}"
        run_id = await _seed_run(department_id=dept_a, stand_id=stand_id, os_version_id=rc)
        resp = await client.post(
            f"{ADD_TEST_BASE}/{run_id}/add-test", headers=_hdr(guest_token), json={"test_id": test_id},
        )
        assert resp.status_code == 403
