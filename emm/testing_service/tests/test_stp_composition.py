"""Тесты состава СТП: явный `scope`, идемпотентность, переключение changelog↔full,
`stp_compositions.revision` (§D4/D5 плана миграции).

Переиспользует фикстуры/хелперы `tests.test_stp` (мок Zephyr/secret_client,
сидинг тест-кейсов/интеграционных настроек) и `tests.test_queue` (мок
server_service, создание стенда) — тот же стиль, что и `test_stp.py`.
"""

from __future__ import annotations

import uuid

import pytest

from src.core.constants import StpCellStatus
from src.db.session import AsyncSessionLocal
from src.repositories import stp_cell as stp_cell_repo
from src.repositories import stp_composition as stp_composition_repo
from src.services import changelog_service
from tests.conftest import auth_hdr as _hdr
from tests.test_queue import _create_stand, mock_server_service, recorded_calls  # noqa: F401
from tests.test_stp import (
    STP_BASE,
    _create_test_def_for_dept,
    _seed_integration_settings,
    _seed_stp_test_case,
    mock_os_version_catalog,  # noqa: F401
    mock_secret_client,  # noqa: F401
    mock_zephyr,  # noqa: F401
)

COMPOSITION_BASE = f"{STP_BASE}/composition"


def _mock_changelog(monkeypatch, changed_components: list[str]):
    async def fake_fetch(db, rc: str):
        return changed_components

    monkeypatch.setattr(changelog_service, "fetch_changed_components", fake_fetch)


async def _get_composition(department_id: str, os_version_id: str):
    async with AsyncSessionLocal() as db:
        return await stp_composition_repo.get_by_department_and_os_version(db, department_id, os_version_id)


class TestStpCompositionSwitch:
    async def _seed_two_tests(self, client, admin_token, stand_id, dept_a):
        """Тест `kern` с `changelog_component="kernel"` + тест `pg` с
        `changelog_component="postgresql"`. Дальше по файлу changelog-сервис
        называет изменившимся только `kernel` — `pg` в changelog-объём не
        входит, а в полный входит."""
        _id_kern, code_kern = await _create_test_def_for_dept(client, admin_token, stand_id, dept_a)
        _id_pg, code_pg = await _create_test_def_for_dept(client, admin_token, stand_id, dept_a)
        async with AsyncSessionLocal() as db:
            from src.repositories import test_definition as test_definition_repo

            test_kern = await test_definition_repo.get_by_id(db, _id_kern)
            await test_definition_repo.update(db, test_kern, {"changelog_component": "kernel"})
            test_pg = await test_definition_repo.get_by_id(db, _id_pg)
            await test_definition_repo.update(db, test_pg, {"changelog_component": "postgresql"})
            await db.commit()
        await _seed_stp_test_case(code_kern, zephyr_id="BT-T-KERN")
        await _seed_stp_test_case(code_pg, zephyr_id="BT-T-PG")
        return code_kern, code_pg

    async def test_first_call_creates_composition_revision_1(
        self, client, admin_token, mock_server_service, mock_zephyr, mock_secret_client, dept_a, monkeypatch,
    ):
        mock_server_service()
        _mock_changelog(monkeypatch, changed_components=["kernel"])
        stand_id, _ = await _create_stand(client, admin_token, department_id=dept_a)
        await self._seed_two_tests(client, admin_token, stand_id, dept_a)
        await _seed_integration_settings(dept_a)
        mock_secret_client["cred_x"] = ("jira_bot", "tok123")

        rc = f"1.9.0.{uuid.uuid4().hex[:6]}"
        resp = await client.post(
            f"{STP_BASE}/generate", headers=_hdr(admin_token),
            json={"os_version_id": rc, "mode": "orel", "kernel": "6.1.0", "scope": "changelog", "department_id": dept_a},
        )
        assert resp.status_code == 200, resp.text
        assert len(resp.json()["test_runs"]) == 1
        assert len(mock_zephyr["create"]) == 1

        composition = await _get_composition(dept_a, rc)
        assert composition.scope == "changelog"
        assert composition.revision == 1

    async def test_repeat_same_scope_is_idempotent(
        self, client, admin_token, mock_server_service, mock_zephyr, mock_secret_client, dept_a, monkeypatch,
    ):
        mock_server_service()
        _mock_changelog(monkeypatch, changed_components=["kernel"])
        stand_id, _ = await _create_stand(client, admin_token, department_id=dept_a)
        await self._seed_two_tests(client, admin_token, stand_id, dept_a)
        await _seed_integration_settings(dept_a)
        mock_secret_client["cred_x"] = ("jira_bot", "tok123")
        rc = f"1.9.0.{uuid.uuid4().hex[:6]}"
        body = {"os_version_id": rc, "mode": "orel", "kernel": "6.1.0", "scope": "changelog", "department_id": dept_a}

        first = await client.post(f"{STP_BASE}/generate", headers=_hdr(admin_token), json=body)
        assert first.status_code == 200, first.text
        run_id = first.json()["test_runs"][0]["id"]
        async with AsyncSessionLocal() as db:
            cells_after_first = await stp_cell_repo.list_by_run(db, run_id)

        second = await client.post(f"{STP_BASE}/generate", headers=_hdr(admin_token), json=body)
        assert second.status_code == 200, second.text
        assert second.json()["test_runs"][0]["id"] == run_id

        assert len(mock_zephyr["create"]) == 1  # не создан второй Zephyr-ран
        assert len(mock_zephyr["add"]) == 0  # состав не менялся, добавлять нечего

        async with AsyncSessionLocal() as db:
            cells_after_second = await stp_cell_repo.list_by_run(db, run_id)
        assert len(cells_after_second) == len(cells_after_first) == 1  # только "kernel"-тест

        composition = await _get_composition(dept_a, rc)
        assert composition.revision == 1  # scope не менялся — ревизия не растёт

    async def test_switch_changelog_to_full_adds_missing_cells_without_new_zephyr_run(
        self, client, admin_token, mock_server_service, mock_zephyr, mock_secret_client, dept_a, monkeypatch,
    ):
        mock_server_service()
        _mock_changelog(monkeypatch, changed_components=["kernel"])  # "postgresql" НЕ изменился
        stand_id, _ = await _create_stand(client, admin_token, department_id=dept_a)
        await self._seed_two_tests(client, admin_token, stand_id, dept_a)
        await _seed_integration_settings(dept_a)
        mock_secret_client["cred_x"] = ("jira_bot", "tok123")
        rc = f"1.9.0.{uuid.uuid4().hex[:6]}"

        changelog_resp = await client.post(
            f"{STP_BASE}/generate", headers=_hdr(admin_token),
            json={"os_version_id": rc, "mode": "orel", "kernel": "6.1.0", "scope": "changelog", "department_id": dept_a},
        )
        assert changelog_resp.status_code == 200, changelog_resp.text
        run_id = changelog_resp.json()["test_runs"][0]["id"]
        async with AsyncSessionLocal() as db:
            cells = await stp_cell_repo.list_by_run(db, run_id)
        assert len(cells) == 1  # только "kernel" — "pg" не в changelog-объёме

        full_resp = await client.post(
            f"{STP_BASE}/generate", headers=_hdr(admin_token),
            json={"os_version_id": rc, "mode": "orel", "kernel": "6.1.0", "scope": "full", "department_id": dept_a},
        )
        assert full_resp.status_code == 200, full_resp.text
        assert full_resp.json()["test_runs"][0]["id"] == run_id

        # Новый Zephyr test-run НЕ создан — тест-кейс добавлен в существующий.
        assert len(mock_zephyr["create"]) == 1
        assert len(mock_zephyr["add"]) == 1
        assert mock_zephyr["add"][0]["test_run_key"] == changelog_resp.json()["test_runs"][0]["zephyr_test_run_key"]

        async with AsyncSessionLocal() as db:
            cells = await stp_cell_repo.list_by_run(db, run_id)
        assert len(cells) == 2
        assert all(c.is_active for c in cells)
        assert all(c.status == StpCellStatus.NOT_RUN for c in cells)

        composition = await _get_composition(dept_a, rc)
        assert composition.scope == "full"
        assert composition.revision == 2

    async def test_switch_full_to_changelog_deactivates_preserving_status(
        self, client, admin_token, mock_server_service, mock_zephyr, mock_secret_client, dept_a, monkeypatch,
    ):
        mock_server_service()
        _mock_changelog(monkeypatch, changed_components=["kernel"])  # "postgresql" НЕ изменился
        stand_id, _ = await _create_stand(client, admin_token, department_id=dept_a)
        _code_kern, code_pg = await self._seed_two_tests(client, admin_token, stand_id, dept_a)
        await _seed_integration_settings(dept_a)
        mock_secret_client["cred_x"] = ("jira_bot", "tok123")
        rc = f"1.9.0.{uuid.uuid4().hex[:6]}"

        full_resp = await client.post(
            f"{STP_BASE}/generate", headers=_hdr(admin_token),
            json={"os_version_id": rc, "mode": "orel", "kernel": "6.1.0", "scope": "full", "department_id": dept_a},
        )
        assert full_resp.status_code == 200, full_resp.text
        run_id = full_resp.json()["test_runs"][0]["id"]
        async with AsyncSessionLocal() as db:
            cells = await stp_cell_repo.list_by_run(db, run_id)
        assert len(cells) == 2

        async with AsyncSessionLocal() as db:
            from src.repositories import stp_test_case as _case_repo

            pg_case = await _case_repo.get_by_code(db, code_pg)
        pg_cell_id = next(c.id for c in cells if c.stp_test_case_id == pg_case.id)

        # Симулируем, что "pg" тест уже прошёл, ДО того как объём сузится.
        async with AsyncSessionLocal() as db:
            from src.repositories import stp_cell as _repo

            obj = await _repo.get_by_id(db, pg_cell_id)
            await _repo.update(db, obj, {"status": StpCellStatus.PASSED, "updated_by": "usr_qa"})
            await db.commit()

        changelog_resp = await client.post(
            f"{STP_BASE}/generate", headers=_hdr(admin_token),
            json={"os_version_id": rc, "mode": "orel", "kernel": "6.1.0", "scope": "changelog", "department_id": dept_a},
        )
        assert changelog_resp.status_code == 200, changelog_resp.text
        assert len(mock_zephyr["create"]) == 1  # всё ещё один ран
        assert len(mock_zephyr["add"]) == 0  # деактивация не трогает Zephyr

        async with AsyncSessionLocal() as db:
            cells = await stp_cell_repo.list_by_run(db, run_id)
        by_id = {c.id: c for c in cells}
        deactivated = by_id[pg_cell_id]
        assert deactivated.is_active is False
        assert deactivated.status == StpCellStatus.PASSED  # статус/история сохранены
        assert deactivated.updated_by == "usr_qa"

        active_cells = [c for c in cells if c.is_active]
        assert len(active_cells) == 1  # только "kernel"

        composition = await _get_composition(dept_a, rc)
        assert composition.scope == "changelog"
        assert composition.revision == 2

        # Обратное переключение возвращает ячейку в активный состав без потери истории.
        back_resp = await client.post(
            f"{STP_BASE}/generate", headers=_hdr(admin_token),
            json={"os_version_id": rc, "mode": "orel", "kernel": "6.1.0", "scope": "full", "department_id": dept_a},
        )
        assert back_resp.status_code == 200, back_resp.text
        assert len(mock_zephyr["create"]) == 1
        assert len(mock_zephyr["add"]) == 0  # ячейка уже существовала — просто реактивация

        async with AsyncSessionLocal() as db:
            reactivated = await stp_cell_repo.get_by_id(db, pg_cell_id)
        assert reactivated.is_active is True
        assert reactivated.status == StpCellStatus.PASSED
        assert reactivated.updated_by == "usr_qa"

        composition = await _get_composition(dept_a, rc)
        assert composition.scope == "full"
        assert composition.revision == 3


class TestStpCompositionEndpoint:
    async def test_default_is_empty(self, client, guest_token, dept_a):
        resp = await client.get(
            COMPOSITION_BASE, headers=_hdr(guest_token),
            params={"os_version_id": "1.9.0.no-such-rc", "department_id": dept_a},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["id"] is None
        assert body["scope"] is None
        assert body["revision"] == 0

    async def test_reflects_latest_scope_and_revision(
        self, client, admin_token, guest_token, mock_server_service, mock_zephyr, mock_secret_client, dept_a, monkeypatch,
    ):
        mock_server_service()
        _mock_changelog(monkeypatch, changed_components=["kernel"])
        stand_id, _ = await _create_stand(client, admin_token, department_id=dept_a)
        _id, code = await _create_test_def_for_dept(client, admin_token, stand_id, dept_a)
        async with AsyncSessionLocal() as db:
            from src.repositories import test_definition as test_definition_repo

            obj = await test_definition_repo.get_by_id(db, _id)
            await test_definition_repo.update(db, obj, {"changelog_component": "kernel"})
            await db.commit()
        await _seed_stp_test_case(code, zephyr_id="BT-T1")
        await _seed_integration_settings(dept_a)
        mock_secret_client["cred_x"] = ("jira_bot", "tok123")
        rc = f"1.9.0.{uuid.uuid4().hex[:6]}"

        await client.post(
            f"{STP_BASE}/generate", headers=_hdr(admin_token),
            json={"os_version_id": rc, "mode": "orel", "kernel": "6.1.0", "scope": "changelog", "department_id": dept_a},
        )
        await client.post(
            f"{STP_BASE}/generate", headers=_hdr(admin_token),
            json={"os_version_id": rc, "mode": "orel", "kernel": "6.1.0", "scope": "full", "department_id": dept_a},
        )

        resp = await client.get(
            COMPOSITION_BASE, headers=_hdr(guest_token),
            params={"os_version_id": rc, "department_id": dept_a},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["scope"] == "full"
        assert body["revision"] == 2
