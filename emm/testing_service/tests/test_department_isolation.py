"""Департаментская изоляция read/write-плоскостей testing_service.

Связанные дыры, закрытые вместе:

* **Запись.** `department_report_member`/`department_integration_settings`/
  `department_test_settings` гейтились `require_action` — он смотрит только на
  отдел вызывающего и ничего не знает про `department_id` из URL. Носитель
  `testing_service.admin` отдела A мог переписать данные отдела B.
* **Чтение.** Логи прогонов, стенды, кампании, СТП, HR-ростеры и настройки
  отделов висели на `AuthenticatedIdentity` — дескрипторе, который намеренно
  не проверяет `SERVICE_ACCESS_DENIED`; читать их мог любой аутентифицированный
  актор emm, включая пользователя отдела, которому testing_service вообще не
  выдан.
* **Запись (найдено позже).** `test_definition`/`test_stand` PATCH/DELETE
  гейтились той же матрицей-без-контекста — `admin` отдела A мог изменить или
  удалить тест/стенд отдела B по id, а `test_definition` create/update ещё и
  принимал `department_id` в теле как есть, позволяя завести или увести тест
  «от имени» чужого отдела.

Отдельный класс в конце фиксирует обратное: платформенные каталоги
(`/global-variables`, `/statistics/settings`, `/statistics/status`) остаются
открытыми любому аутентифицированному актору — у них нет `department_id`, их
трогать было нельзя. То же верно для платформенных (`department_id IS NULL`)
`test_definition` — их по-прежнему правит любой носитель `admin`, независимо
от отдела.
"""

from __future__ import annotations

import uuid

import pytest

from src.db.session import AsyncSessionLocal
from src.services import queue as queue_svc
from tests.conftest import auth_hdr as _hdr
from tests.test_queue import (  # noqa: F401 — фикстуры переиспользуются pytest'ом по имени
    LAUNCH_CTX,
    WORKER_SECRET,
    _create_stand,
    _create_test_def,
    _identity,
    _server_hdr,
    configure_internal_keys,
    mock_server_service,
    recorded_calls,
)

API = "/api/testing/v1"
DEP_B = "dep_b"


@pytest.fixture
def dept_b_admin(make_token) -> str:
    """`testing_service.admin`, но в ЧУЖОМ отделе — весь смысл этих тестов."""
    return make_token(department_id=DEP_B, service_roles={"testing_service": ["admin"]})


@pytest.fixture
def dept_b_user(make_token) -> str:
    """Обычный пользователь чужого отдела, без ролей в сервисе."""
    return make_token(department_id=DEP_B)


@pytest.fixture
def outsider_token(make_token) -> str:
    """Пользователь отдела, которому testing_service вообще не выдан."""
    return make_token(department_id="dep_outsider", allowed_services=["server_service"])


def _member_payload(**overrides) -> dict:
    base = {"display_name": "Иванов Иван", "bitbucket_username": f"iv_{uuid.uuid4().hex[:6]}"}
    base.update(overrides)
    return base


async def _dep_a_member(client, admin_token) -> str:
    resp = await client.post(
        f"{API}/departments/dep_a/report-members", headers=_hdr(admin_token), json=_member_payload(),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _dep_a_log(client, admin_token, mock_server_service, configure_internal_keys):
    """Настоящий queue_item отдела dep_a с непустым логом."""
    mock_server_service()
    stand_id, _ = await _create_stand(client, admin_token)
    test_id = await _create_test_def(client, admin_token, stand_id)
    async with AsyncSessionLocal() as db:
        item = await queue_svc.enqueue(db, _identity(), test_id, launch_context=dict(LAUNCH_CTX))
    resp = await client.post(
        f"/internal/queue/{item.id}/log-chunk",
        headers=_server_hdr("testing_worker", WORKER_SECRET),
        json={"text": "секрет отдела A"},
    )
    assert resp.status_code == 200, resp.text
    return item.id, stand_id


# ── Bug 1: cross-department запись ────────────────────────────────────────────


class TestCrossDepartmentWrite:
    async def test_cannot_create_member_in_foreign_department(self, client, admin_token):
        resp = await client.post(
            f"{API}/departments/{DEP_B}/report-members",
            headers=_hdr(admin_token), json=_member_payload(),
        )
        assert resp.status_code == 403, resp.text

    async def test_cannot_update_foreign_member(self, client, admin_token, dept_b_admin):
        member_id = await _dep_a_member(client, admin_token)
        # URL-сегмент отдела подставляет клиент — здесь он «правильный», но
        # строка всё равно чужая, и отдел берётся с неё.
        resp = await client.patch(
            f"{API}/departments/{DEP_B}/report-members/{member_id}",
            headers=_hdr(dept_b_admin), json={"display_name": "Взломано"},
        )
        assert resp.status_code == 403, resp.text

    async def test_cannot_update_foreign_member_even_with_matching_url(
        self, client, admin_token, dept_b_admin,
    ):
        """URL с настоящим отделом строки не помогает — решает `identity.department_id`."""
        member_id = await _dep_a_member(client, admin_token)
        resp = await client.patch(
            f"{API}/departments/dep_a/report-members/{member_id}",
            headers=_hdr(dept_b_admin), json={"display_name": "Взломано"},
        )
        assert resp.status_code == 403, resp.text

    async def test_cannot_delete_foreign_member(self, client, admin_token, dept_b_admin):
        member_id = await _dep_a_member(client, admin_token)
        resp = await client.delete(
            f"{API}/departments/{DEP_B}/report-members/{member_id}", headers=_hdr(dept_b_admin),
        )
        assert resp.status_code == 403, resp.text
        # Строка на месте — отказ не «тихий no-op».
        check = await client.get(
            f"{API}/departments/dep_a/report-members/{member_id}", headers=_hdr(admin_token),
        )
        assert check.status_code == 200, check.text

    async def test_cannot_write_foreign_test_settings(self, client, admin_token):
        resp = await client.put(
            f"{API}/department-test-settings/{DEP_B}",
            headers=_hdr(admin_token), json={"test_username": "attacker"},
        )
        assert resp.status_code == 403, resp.text

    async def test_cannot_write_foreign_integration_settings(self, client, admin_token):
        resp = await client.put(
            f"{API}/department-integration-settings/{DEP_B}",
            headers=_hdr(admin_token), json={"jira_base_url": "https://evil.example"},
        )
        assert resp.status_code == 403, resp.text

    async def test_own_department_write_still_works(self, client, admin_token):
        """Негативные проверки выше не должны были заодно сломать нормальный путь."""
        created = await client.post(
            f"{API}/departments/dep_a/report-members", headers=_hdr(admin_token),
            json=_member_payload(display_name="Свой отдел"),
        )
        assert created.status_code == 201, created.text
        member_id = created.json()["id"]

        patched = await client.patch(
            f"{API}/departments/dep_a/report-members/{member_id}",
            headers=_hdr(admin_token), json={"display_name": "Переименован"},
        )
        assert patched.status_code == 200, patched.text
        assert patched.json()["display_name"] == "Переименован"

        settings = await client.put(
            f"{API}/department-test-settings/dep_a",
            headers=_hdr(admin_token), json={"test_username": "tester"},
        )
        assert settings.status_code == 200, settings.text
        assert settings.json()["test_username"] == "tester"

        integration = await client.put(
            f"{API}/department-integration-settings/dep_a",
            headers=_hdr(admin_token), json={"jira_base_url": "https://jira.example"},
        )
        assert integration.status_code == 200, integration.text

        deleted = await client.delete(
            f"{API}/departments/dep_a/report-members/{member_id}", headers=_hdr(admin_token),
        )
        assert deleted.status_code == 200, deleted.text


# ── Bug 2: read-плоскость ─────────────────────────────────────────────────────


class TestCrossDepartmentReadDepartmentScoped:
    async def test_foreign_roster_list_denied(self, client, admin_token, dept_b_admin):
        await _dep_a_member(client, admin_token)
        resp = await client.get(
            f"{API}/departments/dep_a/report-members", headers=_hdr(dept_b_admin),
        )
        assert resp.status_code == 403, resp.text

    async def test_foreign_roster_item_denied(self, client, admin_token, dept_b_admin):
        member_id = await _dep_a_member(client, admin_token)
        resp = await client.get(
            f"{API}/departments/dep_a/report-members/{member_id}", headers=_hdr(dept_b_admin),
        )
        assert resp.status_code == 403, resp.text

    async def test_foreign_test_settings_denied(self, client, admin_token, dept_b_user):
        await client.put(
            f"{API}/department-test-settings/dep_a",
            headers=_hdr(admin_token), json={"test_username": "secret_user"},
        )
        resp = await client.get(f"{API}/department-test-settings/dep_a", headers=_hdr(dept_b_user))
        assert resp.status_code == 403, resp.text

    async def test_foreign_integration_settings_denied(self, client, dept_b_user):
        resp = await client.get(
            f"{API}/department-integration-settings/dep_a", headers=_hdr(dept_b_user),
        )
        assert resp.status_code == 403, resp.text

    async def test_foreign_sprint_board_denied(self, client, dept_b_user):
        resp = await client.get(
            f"{API}/department-integration-settings/dep_a/sprint-board", headers=_hdr(dept_b_user),
        )
        assert resp.status_code == 403, resp.text

    async def test_own_department_read_still_works(self, client, admin_token, guest_token):
        """Любая роль СВОЕГО отдела читает как раньше — гейт по отделу, не по роли."""
        await _dep_a_member(client, admin_token)
        for path in (
            f"{API}/departments/dep_a/report-members",
            f"{API}/department-test-settings/dep_a",
            f"{API}/department-integration-settings/dep_a",
        ):
            resp = await client.get(path, headers=_hdr(guest_token))
            assert resp.status_code == 200, f"{path}: {resp.text}"


class TestCrossDepartmentReadLogs:
    async def test_foreign_log_text_denied(
        self, client, admin_token, dept_b_admin, mock_server_service, configure_internal_keys,
    ):
        item_id, _stand_id = await _dep_a_log(
            client, admin_token, mock_server_service, configure_internal_keys,
        )
        resp = await client.get(f"{API}/queue-items/{item_id}/log", headers=_hdr(dept_b_admin))
        assert resp.status_code == 403, resp.text
        assert "секрет отдела A" not in resp.text

    async def test_foreign_log_segments_denied(
        self, client, admin_token, dept_b_admin, mock_server_service, configure_internal_keys,
    ):
        item_id, _stand_id = await _dep_a_log(
            client, admin_token, mock_server_service, configure_internal_keys,
        )
        resp = await client.get(
            f"{API}/queue-items/{item_id}/log/segments", headers=_hdr(dept_b_admin),
        )
        assert resp.status_code == 403, resp.text

    async def test_outsider_department_denied(
        self, client, admin_token, outsider_token, mock_server_service, configure_internal_keys,
    ):
        """Отдел без testing_service в `allowed_services` не должен доходить даже до гейта отдела."""
        item_id, _stand_id = await _dep_a_log(
            client, admin_token, mock_server_service, configure_internal_keys,
        )
        resp = await client.get(f"{API}/queue-items/{item_id}/log", headers=_hdr(outsider_token))
        assert resp.status_code == 403, resp.text
        assert resp.json()["error_code"] == "SERVICE_ACCESS_DENIED"

    async def test_own_department_log_still_readable(
        self, client, admin_token, guest_token, mock_server_service, configure_internal_keys,
    ):
        item_id, _stand_id = await _dep_a_log(
            client, admin_token, mock_server_service, configure_internal_keys,
        )
        resp = await client.get(f"{API}/queue-items/{item_id}/log", headers=_hdr(guest_token))
        assert resp.status_code == 200, resp.text
        assert "секрет отдела A" in resp.text

        segments = await client.get(
            f"{API}/queue-items/{item_id}/log/segments", headers=_hdr(guest_token),
        )
        assert segments.status_code == 200, segments.text


class TestCrossDepartmentReadStandsAndRuns:
    async def test_foreign_stand_card_denied(
        self, client, admin_token, dept_b_admin, mock_server_service,
    ):
        mock_server_service()
        stand_id, _ = await _create_stand(client, admin_token)
        resp = await client.get(f"{API}/test-stands/{stand_id}", headers=_hdr(dept_b_admin))
        assert resp.status_code == 403, resp.text

    async def test_foreign_stand_current_queue_item_denied(
        self, client, admin_token, dept_b_admin, mock_server_service,
    ):
        mock_server_service()
        stand_id, _ = await _create_stand(client, admin_token)
        resp = await client.get(
            f"{API}/test-stands/{stand_id}/current-queue-item", headers=_hdr(dept_b_admin),
        )
        assert resp.status_code == 403, resp.text

    async def test_stand_list_scoped_to_own_department(
        self, client, admin_token, dept_b_admin, mock_server_service,
    ):
        mock_server_service()
        stand_id, server_id = await _create_stand(client, admin_token)

        foreign = await client.get(f"{API}/test-stands", headers=_hdr(dept_b_admin))
        assert foreign.status_code == 200, foreign.text
        assert all(item["id"] != stand_id for item in foreign.json()["items"])

        # server_id-lookup тоже не должен сдавать чужой стенд.
        by_server = await client.get(
            f"{API}/test-stands", headers=_hdr(dept_b_admin), params={"server_id": server_id},
        )
        assert by_server.status_code == 200, by_server.text
        assert by_server.json()["items"] == []

        own = await client.get(f"{API}/test-stands", headers=_hdr(admin_token))
        assert own.status_code == 200, own.text
        assert any(item["id"] == stand_id for item in own.json()["items"])

    async def test_explicit_foreign_department_filter_denied(self, client, dept_b_admin):
        for path in (f"{API}/test-stands", f"{API}/test-runs", f"{API}/test-definitions"):
            resp = await client.get(
                path, headers=_hdr(dept_b_admin), params={"department_id": "dep_a"},
            )
            assert resp.status_code == 403, f"{path}: {resp.text}"
            assert resp.json()["error_code"] == "DEPARTMENT_ISOLATION"

    async def test_platform_test_definitions_visible_to_every_department(
        self, client, admin_token, dept_b_admin,
    ):
        """Тесты без `department_id` платформенные — импорт легаси-каталога заводит именно такие."""
        code = f"platform.test.{uuid.uuid4().hex[:8]}"
        created = await client.post(
            f"{API}/test-definitions", headers=_hdr(admin_token),
            json={"code": code, "full_name": "Платформенный тест", "readiness": "ready"},
        )
        assert created.status_code == 201, created.text
        assert created.json()["department_id"] is None
        test_id = created.json()["id"]

        resp = await client.get(f"{API}/test-definitions/{test_id}", headers=_hdr(dept_b_admin))
        assert resp.status_code == 200, resp.text

        listed = await client.get(f"{API}/test-definitions", headers=_hdr(dept_b_admin))
        assert listed.status_code == 200, listed.text
        assert any(item["id"] == test_id for item in listed.json()["items"])

    async def test_department_scoped_test_definition_hidden(
        self, client, admin_token, dept_b_admin,
    ):
        code = f"scoped.test.{uuid.uuid4().hex[:8]}"
        created = await client.post(
            f"{API}/test-definitions", headers=_hdr(admin_token),
            json={
                "code": code, "full_name": "Тест отдела A",
                "readiness": "ready", "department_id": "dep_a",
            },
        )
        assert created.status_code == 201, created.text
        test_id = created.json()["id"]

        by_id = await client.get(f"{API}/test-definitions/{test_id}", headers=_hdr(dept_b_admin))
        assert by_id.status_code == 403, by_id.text

        by_code = await client.get(
            f"{API}/test-definitions/by-code/{code}", headers=_hdr(dept_b_admin),
        )
        assert by_code.status_code == 403, by_code.text

        args = await client.get(
            f"{API}/test-definitions/{test_id}/args", headers=_hdr(dept_b_admin),
        )
        assert args.status_code == 403, args.text

        listed = await client.get(f"{API}/test-definitions", headers=_hdr(dept_b_admin))
        assert listed.status_code == 200, listed.text
        assert all(item["id"] != test_id for item in listed.json()["items"])


# ── Bug 3: cross-department test_definition/test_stand write ─────────────────


async def _dep_a_test_def(client, admin_token, **overrides) -> str:
    payload = {
        "code": f"iso.test.{uuid.uuid4().hex[:8]}",
        "full_name": "Тест изоляции",
        "readiness": "ready",
        "department_id": "dep_a",
    }
    payload.update(overrides)
    resp = await client.post(f"{API}/test-definitions", headers=_hdr(admin_token), json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


class TestCrossDepartmentTestDefinitionWrite:
    async def test_cannot_create_test_definition_with_foreign_department_id(
        self, client, admin_token,
    ):
        resp = await client.post(
            f"{API}/test-definitions", headers=_hdr(admin_token),
            json={
                "code": f"iso.forge.{uuid.uuid4().hex[:8]}", "full_name": "Подделка",
                "readiness": "ready", "department_id": DEP_B,
            },
        )
        assert resp.status_code == 403, resp.text

    async def test_cannot_update_foreign_test_definition(self, client, admin_token, dept_b_admin):
        test_id = await _dep_a_test_def(client, admin_token)
        resp = await client.patch(
            f"{API}/test-definitions/{test_id}", headers=_hdr(dept_b_admin),
            json={"full_name": "Взломано"},
        )
        assert resp.status_code == 403, resp.text

    async def test_cannot_delete_foreign_test_definition(self, client, admin_token, dept_b_admin):
        test_id = await _dep_a_test_def(client, admin_token)
        resp = await client.delete(
            f"{API}/test-definitions/{test_id}", headers=_hdr(dept_b_admin),
        )
        assert resp.status_code == 403, resp.text
        # Строка на месте — отказ не «тихий no-op».
        check = await client.get(f"{API}/test-definitions/{test_id}", headers=_hdr(admin_token))
        assert check.status_code == 200, check.text

    async def test_cannot_reassign_test_definition_to_foreign_department(
        self, client, admin_token,
    ):
        test_id = await _dep_a_test_def(client, admin_token)
        resp = await client.patch(
            f"{API}/test-definitions/{test_id}", headers=_hdr(admin_token),
            json={"department_id": DEP_B},
        )
        assert resp.status_code == 403, resp.text
        # Отдел строки не поменялся.
        check = await client.get(f"{API}/test-definitions/{test_id}", headers=_hdr(admin_token))
        assert check.status_code == 200, check.text
        assert check.json()["department_id"] == "dep_a"

    async def test_platform_test_definition_still_editable_by_any_admin(
        self, client, admin_token, dept_b_admin,
    ):
        """Платформенный тест (`department_id IS NULL`) — общий каталог, изоляцию не накручивали."""
        test_id = await _dep_a_test_def(client, admin_token, department_id=None)
        resp = await client.patch(
            f"{API}/test-definitions/{test_id}", headers=_hdr(dept_b_admin),
            json={"full_name": "Правка платформенного теста"},
        )
        assert resp.status_code == 200, resp.text

    async def test_own_department_test_definition_write_still_works(self, client, admin_token):
        test_id = await _dep_a_test_def(client, admin_token)
        patched = await client.patch(
            f"{API}/test-definitions/{test_id}", headers=_hdr(admin_token),
            json={"full_name": "Переименован"},
        )
        assert patched.status_code == 200, patched.text
        deleted = await client.delete(f"{API}/test-definitions/{test_id}", headers=_hdr(admin_token))
        assert deleted.status_code == 200, deleted.text


class TestCrossDepartmentTestStandWrite:
    async def test_cannot_update_foreign_test_stand(
        self, client, admin_token, dept_b_admin, mock_server_service,
    ):
        mock_server_service()
        stand_id, _ = await _create_stand(client, admin_token)
        resp = await client.patch(
            f"{API}/test-stands/{stand_id}", headers=_hdr(dept_b_admin),
            json={"queue_enabled": False},
        )
        assert resp.status_code == 403, resp.text

    async def test_cannot_delete_foreign_test_stand(
        self, client, admin_token, dept_b_admin, mock_server_service,
    ):
        mock_server_service()
        stand_id, _ = await _create_stand(client, admin_token)
        resp = await client.delete(f"{API}/test-stands/{stand_id}", headers=_hdr(dept_b_admin))
        assert resp.status_code == 403, resp.text
        # Строка на месте — отказ не «тихий no-op».
        check = await client.get(f"{API}/test-stands/{stand_id}", headers=_hdr(admin_token))
        assert check.status_code == 200, check.text

    async def test_own_department_test_stand_write_still_works(
        self, client, admin_token, mock_server_service,
    ):
        mock_server_service()
        stand_id, _ = await _create_stand(client, admin_token)
        patched = await client.patch(
            f"{API}/test-stands/{stand_id}", headers=_hdr(admin_token),
            json={"queue_enabled": False},
        )
        assert patched.status_code == 200, patched.text
        assert patched.json()["queue_enabled"] is False
        deleted = await client.delete(f"{API}/test-stands/{stand_id}", headers=_hdr(admin_token))
        assert deleted.status_code == 200, deleted.text


# ── Платформенные каталоги: изоляцию НЕ накручивали ──────────────────────────


class TestPlatformWideCatalogsStillOpen:
    """Регрессия на то, что чинить было нельзя.

    У `global_variables`/`statistics_settings`/`statistics_recalc` нет колонки
    `department_id` — это общие справочники платформы. Они остаются на
    `AuthenticatedIdentity`: читает любой аутентифицированный актор, включая
    того, чьему отделу testing_service не выдан вовсе.
    """

    async def test_global_variables_open_to_role_less_user(self, client, no_role_token):
        listed = await client.get(f"{API}/global-variables", headers=_hdr(no_role_token))
        assert listed.status_code == 200, listed.text
        assert listed.json()["total"] >= 1

    async def test_global_variables_open_to_outsider_department(self, client, outsider_token):
        listed = await client.get(f"{API}/global-variables", headers=_hdr(outsider_token))
        assert listed.status_code == 200, listed.text

        items = listed.json()["items"]
        assert items, "сид-миграция обязана оставить хотя бы одну переменную"
        by_id = await client.get(
            f"{API}/global-variables/{items[0]['id']}", headers=_hdr(outsider_token),
        )
        assert by_id.status_code == 200, by_id.text

    async def test_global_variables_still_reject_anonymous(self, client):
        resp = await client.get(f"{API}/global-variables")
        assert resp.status_code == 401, resp.text

    async def test_statistics_settings_and_status_open(self, client, no_role_token, outsider_token):
        for token in (no_role_token, outsider_token):
            settings = await client.get(f"{API}/statistics/settings", headers=_hdr(token))
            assert settings.status_code == 200, settings.text
            status = await client.get(f"{API}/statistics/status", headers=_hdr(token))
            assert status.status_code == 200, status.text
