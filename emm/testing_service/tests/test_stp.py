"""Тесты СТП: changelog-фильтр, генерация Zephyr test-run'ов, событийный статус,
ручной override, RBAC на новые эндпоинты, department_integration_settings CRUD
(§1, §2.5, §6, §7 плана миграции).

Все внешние вызовы (changelog-сервис, secret_service, Jira/Zephyr) мокаются —
`httpx.MockTransport`/monkeypatch на модульные функции, ни одного реального
сетевого вызова. `server_client`-моки и хелперы очереди переиспользуются из
`tests.test_queue` — событийный хук статуса СТП вплетён в тот же
`complete_item`, который эти тесты уже гоняют end-to-end.
"""

from __future__ import annotations

import uuid

import httpx
import pytest

from src.core.constants import StpCellStatus
from src.db.session import AsyncSessionLocal
from src.repositories import department_integration_settings as dis_repo
from src.repositories import stp_cell as stp_cell_repo
from src.repositories import stp_test_case as stp_test_case_repo
from src.repositories import stp_test_run as stp_test_run_repo
from src.services import changelog_service, secret_client, server_client, stp as stp_svc, zephyr_client
from src.services import queue as queue_svc
from src.utils.ids import (
    department_integration_settings_id,
    stp_cell_id,
    stp_test_case_id,
    stp_test_run_id,
)
from tests.conftest import auth_hdr as _hdr
from tests.test_queue import (
    CALLBACK_BASE,
    LAUNCH_CTX,
    QUEUE_BASE,
    SERVER_SECRET,
    TESTS_BASE,
    WORKER_SECRET,
    _create_stand,
    _identity,
    _server_hdr,
    configure_internal_keys,
    mock_server_service,
    recorded_calls,  # noqa: F401 — не используется напрямую, но mock_server_service
    # объявляет её как свою фикстурную зависимость; pytest резолвит фикстуры по
    # именам в namespace ЭТОГО модуля, импорт обязателен, даже без прямого use.
)

STP_BASE = "/api/testing/v1/stp"
DIS_BASE = "/api/testing/v1/department-integration-settings"


async def _create_test_def_for_dept(
    client, admin_token, pinned_stand_id: str, department_id: str, *, mode: str = "orel",
) -> tuple[str, str]:
    """Как `tests.test_queue._create_test_def`, но с явным `department_id` —
    нужен генерации СТП (`test_definition_repo.list_by_department_pinned`
    фильтрует строго по department_id, дефолт `None` в него не попадает).
    Возвращает `(test_id, code)`.
    """
    code = f"stp.test.{uuid.uuid4().hex[:8]}"
    resp = await client.post(
        TESTS_BASE, headers=_hdr(admin_token),
        json={
            "code": code, "full_name": "STP test", "department_id": department_id,
            "readiness": "ready", "mode": mode,
            "pinned_stand_id": pinned_stand_id,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"], code


# ── changelog-фильтр (§1, §7) ────────────────────────────────────────────────


class TestChangelogFilter:
    def test_scope_is_explicit_not_guessed_from_rc(self):
        # §D4: full/changelog — явный параметр, не производная от вида RC.
        assert stp_svc._is_full_scope("full") is True
        assert stp_svc._is_full_scope("changelog") is False

    async def test_full_scope_skips_changelog_service_regardless_of_rc_shape(self, monkeypatch):
        from src.models import TestDefinition

        t = TestDefinition(id="t1", code="a", full_name="A", changelog_component="kernel")
        called = False

        async def fake_fetch(db, rc: str):
            nonlocal called
            called = True
            return []

        monkeypatch.setattr(changelog_service, "fetch_changed_components", fake_fetch)
        async with AsyncSessionLocal() as db:
            # RC вида "X.Y.Z.1" — раньше по этой строке угадывался full scope;
            # теперь это никак не влияет, только явный scope="full".
            result = await stp_svc._filter_by_changelog(db, [t], "1.8.5.1UU", "full")
        assert result == [t]
        assert called is False

    async def test_filter_keeps_tests_without_component(self):
        from src.models import TestDefinition

        t = TestDefinition(id="t1", code="a", full_name="A", changelog_component=None)
        async with AsyncSessionLocal() as db:
            result = await stp_svc._filter_by_changelog(db, [t], "1.8.5.46-nocache1", "changelog")
        # changelog service not configured in tests → fetch_changed_components
        # returns None → safe default is "keep everything" regardless of component.
        assert result == [t]

    async def test_filter_by_changed_components(self, monkeypatch):
        from src.models import TestDefinition

        kept = TestDefinition(id="t1", code="a", full_name="A", changelog_component="kernel")
        dropped = TestDefinition(id="t2", code="b", full_name="B", changelog_component="postgresql")
        no_component = TestDefinition(id="t3", code="c", full_name="C", changelog_component=None)

        async def fake_fetch(db, rc: str):
            return ["kernel"]

        monkeypatch.setattr(changelog_service, "fetch_changed_components", fake_fetch)
        async with AsyncSessionLocal() as db:
            result = await stp_svc._filter_by_changelog(
                db, [kept, dropped, no_component], "1.8.5.46", "changelog",
            )
        assert result == [kept, no_component]

    async def test_changelog_service_non_success_status_yields_empty_result(self, monkeypatch):
        settings_stub = type("S", (), {
            "changelog_service_url": "http://changelog", "changelog_request_timeout_seconds": 2.0,
            "changelog_cache_ttl_seconds": 7776000.0,
        })()
        monkeypatch.setattr(changelog_service, "get_settings", lambda: settings_stub)

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"status": "error", "result": []})

        monkeypatch.setattr(
            changelog_service, "build_client",
            lambda timeout: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )
        async with AsyncSessionLocal() as db:
            result = await changelog_service.fetch_changed_components(db, "1.8.5.46-error")
        assert result == []

    async def test_changelog_service_success_extracts_components(self, monkeypatch):
        settings_stub = type("S", (), {
            "changelog_service_url": "http://changelog", "changelog_request_timeout_seconds": 2.0,
            "changelog_cache_ttl_seconds": 7776000.0,
        })()
        monkeypatch.setattr(changelog_service, "get_settings", lambda: settings_stub)

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={
                "status": "success",
                "result": ["kernel", {"component": "postgresql"}, {"name": "openssl"}, {}],
            })

        monkeypatch.setattr(
            changelog_service, "build_client",
            lambda timeout: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )
        async with AsyncSessionLocal() as db:
            result = await changelog_service.fetch_changed_components(db, "1.8.5.46-success")
        assert result == ["kernel", "postgresql", "openssl"]

    async def test_changelog_service_caches_response_and_skips_second_call(self, monkeypatch):
        settings_stub = type("S", (), {
            "changelog_service_url": "http://changelog", "changelog_request_timeout_seconds": 2.0,
            "changelog_cache_ttl_seconds": 7776000.0,
        })()
        monkeypatch.setattr(changelog_service, "get_settings", lambda: settings_stub)

        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return httpx.Response(200, json={"status": "success", "result": ["kernel"]})

        monkeypatch.setattr(
            changelog_service, "build_client",
            lambda timeout: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )
        rc = f"1.8.5.46-cache-{uuid.uuid4().hex[:8]}"
        async with AsyncSessionLocal() as db:
            first = await changelog_service.fetch_changed_components(db, rc)
        async with AsyncSessionLocal() as db:
            second = await changelog_service.fetch_changed_components(db, rc)
        assert first == ["kernel"]
        assert second == ["kernel"]
        assert call_count == 1

    async def test_changelog_service_falls_back_to_stale_cache_on_network_error(self, monkeypatch):
        settings_stub = type("S", (), {
            "changelog_service_url": "http://changelog", "changelog_request_timeout_seconds": 2.0,
            "changelog_cache_ttl_seconds": 7776000.0,
        })()
        monkeypatch.setattr(changelog_service, "get_settings", lambda: settings_stub)

        rc = f"1.8.5.46-stale-{uuid.uuid4().hex[:8]}"

        def ok_handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"status": "success", "result": ["kernel"]})

        monkeypatch.setattr(
            changelog_service, "build_client",
            lambda timeout: httpx.AsyncClient(transport=httpx.MockTransport(ok_handler)),
        )
        async with AsyncSessionLocal() as db:
            await changelog_service.fetch_changed_components(db, rc)

        def failing_handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("boom", request=request)

        monkeypatch.setattr(
            changelog_service, "build_client",
            lambda timeout: httpx.AsyncClient(transport=httpx.MockTransport(failing_handler)),
        )
        monkeypatch.setattr(changelog_service, "_is_fresh", lambda fetched_at, ttl: False)
        async with AsyncSessionLocal() as db:
            result = await changelog_service.fetch_changed_components(db, rc)
        assert result == ["kernel"]

    def test_not_configured_returns_none_shape(self):
        assert changelog_service.is_configured() is False


# ── Папка Zephyr: глубина release ───────────────────────────────────────────


class TestDeriveRelease:
    """Папка рана — `/stress_test/{release}/{rc}`; глубина `release` должна
    совпадать с легаси (`liballta.py::TestrunManager.create_test_run`), иначе
    новые раны садятся в другую ветку дерева папок Jira, чем легаси-раны того
    же РЦ."""

    def test_ordinary_four_segment_release_keeps_three(self):
        assert stp_svc._derive_release("1.8.5.46") == "1.8.5"

    def test_folder_matches_legacy_example(self):
        rc = "1.8.5.46"
        assert f"/stress_test/{stp_svc._derive_release(rc)}/{rc}" == "/stress_test/1.8.5/1.8.5.46"

    def test_uu_hotfix_keeps_five_segments(self):
        assert stp_svc._derive_release("1.7.3.UU.1.2") == "1.7.3.UU.1"

    def test_six_segments_without_uu_marker_is_ordinary(self):
        assert stp_svc._derive_release("1.8.5.46.7.8") == "1.8.5"

    def test_short_format_falls_back_without_raising(self):
        assert stp_svc._derive_release("1.8") == "1.8"

    def test_pull_from_life_copy_agrees(self):
        """Дубль в `stp_pull_from_life` обязан давать то же самое — иначе
        поиск ходит не в ту папку, в которую пишет генерация."""
        from src.services import stp_pull_from_life as pull_svc

        for rc in ("1.8.5.46", "1.7.3.UU.1.2", "1.8.5.46.7.8", "1.8", "1.8.5"):
            assert stp_svc._derive_release(rc) == pull_svc._derive_release(rc), rc


# ── Zephyr client — retry без environment на 400 ────────────────────────────


class TestZephyrClientCreateTestRun:
    async def test_retries_without_environment_on_400(self, monkeypatch):
        settings_stub = type("S", (), {"zephyr_request_timeout_seconds": 2.0})()
        monkeypatch.setattr(zephyr_client, "get_settings", lambda: settings_stub)

        calls: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            import json as _json

            body = _json.loads(request.content)
            calls.append(body)
            has_env = any("environment" in item for item in body["items"])
            if has_env:
                return httpx.Response(
                    400,
                    json={"message": "environment was not found for field environment on project BT"},
                )
            return httpx.Response(201, json={"key": "BT-R100"})

        monkeypatch.setattr(
            zephyr_client, "build_client",
            lambda timeout: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )
        key = await zephyr_client.create_test_run(
            base_url="http://jira.example",
            bearer_token="tok",
            folder="/stress_test/1.8.5/1.8.5.46",
            name="1.8.5.46_orel_6.1.0_stand1",
            items=[zephyr_client.ZephyrRunItem(test_case_key="BT-T1", environment="6.1.0")],
        )
        assert key == "BT-R100"
        assert len(calls) == 2
        assert "environment" in calls[0]["items"][0]
        assert "environment" not in calls[1]["items"][0]

    async def test_create_test_run_failure_raises(self, monkeypatch):
        settings_stub = type("S", (), {"zephyr_request_timeout_seconds": 2.0})()
        monkeypatch.setattr(zephyr_client, "get_settings", lambda: settings_stub)

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, json={"message": "boom"})

        monkeypatch.setattr(
            zephyr_client, "build_client",
            lambda timeout: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )
        from src.core.exceptions import ServiceUnavailableError

        with pytest.raises(ServiceUnavailableError):
            await zephyr_client.create_test_run(
                base_url="http://jira.example", bearer_token="tok",
                folder="/f", name="n", items=[zephyr_client.ZephyrRunItem(test_case_key="BT-T1")],
            )


# ── /stp/test-cases CRUD + RBAC ──────────────────────────────────────────────


class TestStpTestCasesCrud:
    async def test_admin_can_create_and_read(self, client, admin_token):
        code = f"stp.case.{uuid.uuid4().hex[:8]}"
        resp = await client.post(
            f"{STP_BASE}/test-cases", headers=_hdr(admin_token),
            json={"code": code, "title": "Postgres balance", "zephyr_id": "BT-T7555"},
        )
        assert resp.status_code == 201, resp.text
        case_id = resp.json()["id"]

        get_resp = await client.get(f"{STP_BASE}/test-cases/{case_id}", headers=_hdr(admin_token))
        assert get_resp.status_code == 200
        assert get_resp.json()["zephyr_id"] == "BT-T7555"

    async def test_guest_cannot_create(self, client, guest_token):
        resp = await client.post(
            f"{STP_BASE}/test-cases", headers=_hdr(guest_token),
            json={"code": f"stp.case.{uuid.uuid4().hex[:8]}", "title": "X"},
        )
        assert resp.status_code == 403, resp.text

    async def test_guest_can_list(self, client, guest_token, admin_token):
        code = f"stp.case.{uuid.uuid4().hex[:8]}"
        await client.post(
            f"{STP_BASE}/test-cases", headers=_hdr(admin_token),
            json={"code": code, "title": "X"},
        )
        resp = await client.get(f"{STP_BASE}/test-cases", headers=_hdr(guest_token))
        assert resp.status_code == 200
        assert any(i["code"] == code for i in resp.json()["items"])

    async def test_duplicate_code_conflict(self, client, admin_token):
        code = f"stp.case.{uuid.uuid4().hex[:8]}"
        payload = {"code": code, "title": "X"}
        first = await client.post(f"{STP_BASE}/test-cases", headers=_hdr(admin_token), json=payload)
        assert first.status_code == 201
        second = await client.post(f"{STP_BASE}/test-cases", headers=_hdr(admin_token), json=payload)
        assert second.status_code == 409

    async def test_update_and_delete(self, client, admin_token):
        code = f"stp.case.{uuid.uuid4().hex[:8]}"
        created = await client.post(
            f"{STP_BASE}/test-cases", headers=_hdr(admin_token), json={"code": code, "title": "X"},
        )
        case_id = created.json()["id"]

        patched = await client.patch(
            f"{STP_BASE}/test-cases/{case_id}", headers=_hdr(admin_token),
            json={"zephyr_id": "BT-T1"},
        )
        assert patched.status_code == 200
        assert patched.json()["zephyr_id"] == "BT-T1"

        deleted = await client.delete(f"{STP_BASE}/test-cases/{case_id}", headers=_hdr(admin_token))
        assert deleted.status_code == 200
        missing = await client.get(f"{STP_BASE}/test-cases/{case_id}", headers=_hdr(admin_token))
        assert missing.status_code == 404


# ── /department-integration-settings CRUD ───────────────────────────────────


@pytest.fixture
def mock_secret_metadata(monkeypatch):
    """Включает валидацию ссылок (C4) и мокает `get_credential_metadata`.

    `{cred_id: {"scope": ...}}` — отсутствие ключа моделирует 404.
    """
    store: dict[str, dict] = {}

    async def fake_get_metadata(token: str, cred_id: str):
        if cred_id not in store:
            from src.core.exceptions import NotFoundError

            raise NotFoundError(error_code="CREDENTIAL_NOT_FOUND", message="not found")
        return store[cred_id]

    monkeypatch.setattr(secret_client, "is_configured", lambda: True)
    monkeypatch.setattr(secret_client, "get_credential_metadata", fake_get_metadata)
    return store


class TestDepartmentIntegrationSettings:
    async def test_default_is_empty(self, client, guest_token, dept_a):
        resp = await client.get(f"{DIS_BASE}/{dept_a}", headers=_hdr(guest_token))
        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] is None
        assert body["credential_id"] is None

    async def test_guest_cannot_write(self, client, guest_token, dept_a):
        resp = await client.put(
            f"{DIS_BASE}/{dept_a}", headers=_hdr(guest_token),
            json={"jira_base_url": "http://jira.example"},
        )
        assert resp.status_code == 403

    async def test_admin_upsert_roundtrip(self, client, admin_token, dept_a):
        put_resp = await client.put(
            f"{DIS_BASE}/{dept_a}", headers=_hdr(admin_token),
            json={"credential_id": "cred_x", "jira_base_url": "http://jira.example"},
        )
        assert put_resp.status_code == 200, put_resp.text
        assert put_resp.json()["credential_id"] == "cred_x"

        get_resp = await client.get(f"{DIS_BASE}/{dept_a}", headers=_hdr(admin_token))
        assert get_resp.json()["jira_base_url"] == "http://jira.example"

        patch_resp = await client.put(
            f"{DIS_BASE}/{dept_a}", headers=_hdr(admin_token),
            json={"confluence_base_url": "http://confluence.example"},
        )
        assert patch_resp.status_code == 200
        assert patch_resp.json()["credential_id"] == "cred_x"
        assert patch_resp.json()["confluence_base_url"] == "http://confluence.example"

    async def test_confluence_credential_id_roundtrip(self, client, admin_token, dept_a):
        resp = await client.put(
            f"{DIS_BASE}/{dept_a}", headers=_hdr(admin_token),
            json={"confluence_credential_id": "cred_confluence"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["confluence_credential_id"] == "cred_confluence"

    async def test_service_scope_credential_is_accepted(self, client, admin_token, dept_a, mock_secret_metadata):
        mock_secret_metadata["cred_service"] = {"scope": "service", "owner_dept_id": dept_a}
        resp = await client.put(
            f"{DIS_BASE}/{dept_a}", headers=_hdr(admin_token),
            json={"credential_id": "cred_service"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["credential_id"] == "cred_service"

    async def test_personal_scope_credential_is_rejected(self, client, admin_token, dept_a, mock_secret_metadata):
        mock_secret_metadata["cred_personal"] = {"scope": "personal", "owner_user_id": "usr_someone"}
        resp = await client.put(
            f"{DIS_BASE}/{dept_a}", headers=_hdr(admin_token),
            json={"confluence_credential_id": "cred_personal"},
        )
        assert resp.status_code == 422, resp.text
        assert resp.json()["error_code"] == "CREDENTIAL_SCOPE_INVALID"

    async def test_credential_not_visible_is_rejected(self, client, admin_token, dept_a, mock_secret_metadata):
        resp = await client.put(
            f"{DIS_BASE}/{dept_a}", headers=_hdr(admin_token),
            json={"bitbucket_credential_id": "cred_missing"},
        )
        assert resp.status_code == 404, resp.text
        assert resp.json()["error_code"] == "CREDENTIAL_NOT_FOUND"


# ── /stp/generate ────────────────────────────────────────────────────────────


async def _seed_integration_settings(
    department_id: str, *, credential_id="cred_x", jira_base_url="http://jira.example",
    bitbucket_credential_id=None,
):
    async with AsyncSessionLocal() as db:
        await dis_repo.create(db, {
            "id": department_integration_settings_id(),
            "department_id": department_id,
            "credential_id": credential_id,
            "jira_base_url": jira_base_url,
            "confluence_base_url": None,
            "bitbucket_credential_id": bitbucket_credential_id,
        })
        await db.commit()


async def _seed_stp_test_case(code: str, *, zephyr_id="BT-T1"):
    async with AsyncSessionLocal() as db:
        obj = await stp_test_case_repo.create(db, {
            "id": stp_test_case_id(), "code": code, "title": code, "zephyr_id": zephyr_id,
            "department_id": None, "created_by": "usr_test",
        })
        await db.commit()
        return obj.id


@pytest.fixture(autouse=True)
def mock_os_version_catalog(monkeypatch):
    """`{os_version_id: name}` — карточка версии из server_service.

    Autouse: `generate_stp_runs`/`_search_folder` резолвят человеческий номер
    РЦ через `server_client.get_os_version` перед тем, как строить папку/имя
    Zephyr-рана. Дефолт `name == id` сохраняет старые фикстуры, где id уже
    записан человеческой версией (`"1.8.5.46"`). Дублирует одноимённую
    фикстуру `test_stp_matrix.py`/`test_run_summary.py` — тот же приём, что и
    `_derive_release` (крохотный хелпер дублируется, не импортируется).
    """
    names: dict[str, str] = {}

    async def fake_get_os_version(os_version_id: str) -> dict:
        return {"id": os_version_id, "name": names.get(os_version_id, os_version_id)}

    monkeypatch.setattr(server_client, "get_os_version", fake_get_os_version)
    return names


@pytest.fixture
def mock_zephyr(monkeypatch):
    calls = {"create": [], "resolve_user": [], "add": []}

    async def fake_create_test_run(*, base_url, bearer_token, folder, name, items, project_key="BT"):
        calls["create"].append({
            "base_url": base_url, "bearer_token": bearer_token, "folder": folder,
            "name": name, "items": items,
        })
        return f"BT-R{len(calls['create'])}"

    async def fake_resolve_user_key(*, base_url, bearer_token, username):
        calls["resolve_user"].append(username)
        return f"jira_{username}"

    async def fake_add_test_cases_to_run(*, base_url, bearer_token, test_run_key, items):
        calls["add"].append({
            "base_url": base_url, "bearer_token": bearer_token,
            "test_run_key": test_run_key, "items": items,
        })

    from src.services import zephyr_client as zc

    monkeypatch.setattr(zc, "create_test_run", fake_create_test_run)
    monkeypatch.setattr(zc, "resolve_user_key", fake_resolve_user_key)
    monkeypatch.setattr(zc, "add_test_cases_to_run", fake_add_test_cases_to_run)
    return calls


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


class TestStpGenerate:
    async def test_full_scope_generates_run_with_cells(
        self, client, admin_token, mock_server_service, mock_zephyr, mock_secret_client, dept_a,
    ):
        mock_server_service()
        stand_id, _ = await _create_stand(client, admin_token, department_id=dept_a)
        _test_id, code = await _create_test_def_for_dept(client, admin_token, stand_id, dept_a)
        await _seed_stp_test_case(code, zephyr_id="BT-T1")
        mock_secret_client["cred_x"] = ("jira_bot", "tok123")
        await _seed_integration_settings(dept_a)

        resp = await client.post(
            f"{STP_BASE}/generate", headers=_hdr(admin_token),
            json={"os_version_id": "1.8.5.46", "mode": "orel", "kernel": "6.1.0", "scope": "full", "department_id": dept_a},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["errors"] == []
        assert len(body["test_runs"]) == 1
        run = body["test_runs"][0]
        assert run["stand_id"] == stand_id
        assert run["zephyr_test_run_key"] == "BT-R1"

        async with AsyncSessionLocal() as db:
            cells = await stp_cell_repo.list_by_run(db, run["id"])
        assert len(cells) == 1
        assert cells[0].status == StpCellStatus.NOT_RUN

    async def test_run_name_uses_stand_alias_when_present(
        self, client, admin_token, mock_server_service, mock_zephyr, mock_secret_client, dept_a,
    ):
        """Четвёртый токен имени рана — `stand3`, как у легаси, а не uuid.

        По нему же `stp_pull_from_life` разбирает раны обратно, поэтому
        расхождение здесь ломает выгрузку из life.
        """
        mock_server_service()
        stand_id, _ = await _create_stand(
            client, admin_token, department_id=dept_a, legacy_token="stand3",
        )
        _test_id, code = await _create_test_def_for_dept(client, admin_token, stand_id, dept_a)
        await _seed_stp_test_case(code, zephyr_id="BT-T1")
        mock_secret_client["cred_x"] = ("jira_bot", "tok123")
        await _seed_integration_settings(dept_a)

        resp = await client.post(
            f"{STP_BASE}/generate", headers=_hdr(admin_token),
            json={"os_version_id": "1.8.5.46", "mode": "orel", "kernel": "6.1.0", "scope": "full", "department_id": dept_a},
        )
        assert resp.status_code == 200, resp.text
        assert mock_zephyr["create"][0]["name"] == "1.8.5.46_orel_6.1.0_stand3"

    async def test_run_name_falls_back_to_stand_id_without_alias(
        self, client, admin_token, mock_server_service, mock_zephyr, mock_secret_client, dept_a,
    ):
        mock_server_service()
        stand_id, _ = await _create_stand(client, admin_token, department_id=dept_a)
        _test_id, code = await _create_test_def_for_dept(client, admin_token, stand_id, dept_a)
        await _seed_stp_test_case(code, zephyr_id="BT-T1")
        mock_secret_client["cred_x"] = ("jira_bot", "tok123")
        await _seed_integration_settings(dept_a)

        resp = await client.post(
            f"{STP_BASE}/generate", headers=_hdr(admin_token),
            json={"os_version_id": "1.8.5.46", "mode": "orel", "kernel": "6.1.0", "scope": "full", "department_id": dept_a},
        )
        assert resp.status_code == 200, resp.text
        assert mock_zephyr["create"][0]["name"] == f"1.8.5.46_orel_6.1.0_{stand_id}"

    async def test_full_scope_excludes_non_ready_tests(
        self, client, admin_token, mock_server_service, mock_zephyr, mock_secret_client, dept_a,
    ):
        mock_server_service()
        stand_id, _ = await _create_stand(client, admin_token, department_id=dept_a)
        ready_id, ready_code = await _create_test_def_for_dept(client, admin_token, stand_id, dept_a)
        broken_id, broken_code = await _create_test_def_for_dept(client, admin_token, stand_id, dept_a)
        patch = await client.patch(
            f"{TESTS_BASE}/{broken_id}", headers=_hdr(admin_token), json={"readiness": "broken"},
        )
        assert patch.status_code == 200, patch.text
        await _seed_stp_test_case(ready_code, zephyr_id="BT-T1")
        await _seed_stp_test_case(broken_code, zephyr_id="BT-T2")
        mock_secret_client["cred_x"] = ("jira_bot", "tok123")
        await _seed_integration_settings(dept_a)

        resp = await client.post(
            f"{STP_BASE}/generate", headers=_hdr(admin_token),
            json={"os_version_id": "1.8.5.46", "mode": "orel", "kernel": "6.1.0", "scope": "full", "department_id": dept_a},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["errors"] == []
        run = body["test_runs"][0]
        async with AsyncSessionLocal() as db:
            cells = await stp_cell_repo.list_by_run(db, run["id"])
            case = await stp_test_case_repo.get_by_code(db, ready_code)
        # Только «Рабочий» тест попал в состав — ровно одна ячейка, и это он.
        assert len(cells) == 1
        assert cells[0].stp_test_case_id == case.id

    async def test_missing_integration_settings_is_partial_error(
        self, client, admin_token, mock_server_service, mock_zephyr, mock_secret_client, dept_a,
    ):
        mock_server_service()
        stand_id, _ = await _create_stand(client, admin_token, department_id=dept_a)
        _test_id, code = await _create_test_def_for_dept(client, admin_token, stand_id, dept_a)
        await _seed_stp_test_case(code, zephyr_id="BT-T1")
        # Настройки интеграции НЕ заведены для dept_a.

        resp = await client.post(
            f"{STP_BASE}/generate", headers=_hdr(admin_token),
            json={"os_version_id": "1.8.5.46", "mode": "orel", "kernel": "6.1.0", "scope": "full", "department_id": dept_a},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["test_runs"] == []
        assert len(body["errors"]) == 1
        assert body["errors"][0]["stand_id"] == stand_id
        assert body["errors"][0]["error_code"] == "JIRA_INTEGRATION_NOT_AVAILABLE"

    async def test_one_stand_failure_does_not_block_another(
        self, client, admin_token, mock_server_service, mock_secret_client, dept_a, monkeypatch,
    ):
        mock_server_service()
        stand_ok, _ = await _create_stand(client, admin_token, department_id=dept_a)
        stand_broken, _ = await _create_stand(client, admin_token, department_id=dept_a)
        _test_ok, code_ok = await _create_test_def_for_dept(client, admin_token, stand_ok, dept_a)
        _test_broken, code_broken = await _create_test_def_for_dept(client, admin_token, stand_broken, dept_a)
        await _seed_stp_test_case(code_ok, zephyr_id="BT-T1")
        await _seed_stp_test_case(code_broken, zephyr_id="BT-T2")
        await _seed_integration_settings(dept_a)
        mock_secret_client["cred_x"] = ("jira_bot", "tok123")

        from src.core.exceptions import ServiceUnavailableError
        from src.services import zephyr_client as zc

        async def selective_create(*, base_url, bearer_token, folder, name, items, project_key="BT"):
            if any(i.test_case_key == "BT-T2" for i in items):
                raise ServiceUnavailableError(error_code="ZEPHYR_CREATE_TEST_RUN_FAILED", message="boom")
            return "BT-R-OK"

        async def fake_resolve_user_key(*, base_url, bearer_token, username):
            return None

        monkeypatch.setattr(zc, "create_test_run", selective_create)
        monkeypatch.setattr(zc, "resolve_user_key", fake_resolve_user_key)

        resp = await client.post(
            f"{STP_BASE}/generate", headers=_hdr(admin_token),
            json={
                "os_version_id": "1.8.5.1", "mode": "orel", "kernel": "6.1.0",
                "scope": "full", "department_id": dept_a,
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert len(body["test_runs"]) == 1
        assert body["test_runs"][0]["stand_id"] == stand_ok
        assert len(body["errors"]) == 1
        assert body["errors"][0]["stand_id"] == stand_broken
        assert body["errors"][0]["error_code"] == "ZEPHYR_CREATE_TEST_RUN_FAILED"

    async def test_full_scope_partitions_tests_by_own_mode(
        self, client, admin_token, mock_server_service, mock_zephyr, mock_secret_client, dept_a,
    ):
        """Смоленск-тест не должен попадать в orel-прогон стенда и наоборот
        (§ mode is a fixed property of the test, not of the STP run)."""
        mock_server_service()
        stand_id, _ = await _create_stand(client, admin_token, department_id=dept_a)
        _orel_id, orel_code = await _create_test_def_for_dept(client, admin_token, stand_id, dept_a)
        _smolensk_id, smolensk_code = await _create_test_def_for_dept(
            client, admin_token, stand_id, dept_a, mode="smolensk",
        )
        await _seed_stp_test_case(orel_code, zephyr_id="BT-T1")
        await _seed_stp_test_case(smolensk_code, zephyr_id="BT-T2")
        mock_secret_client["cred_x"] = ("jira_bot", "tok123")
        await _seed_integration_settings(dept_a)

        orel_resp = await client.post(
            f"{STP_BASE}/generate", headers=_hdr(admin_token),
            json={"os_version_id": "1.8.5.46", "mode": "orel", "kernel": "6.1.0", "scope": "full", "department_id": dept_a},
        )
        assert orel_resp.status_code == 200, orel_resp.text
        orel_body = orel_resp.json()
        assert orel_body["errors"] == []
        assert len(orel_body["test_runs"]) == 1

        smolensk_resp = await client.post(
            f"{STP_BASE}/generate", headers=_hdr(admin_token),
            json={"os_version_id": "1.8.5.46", "mode": "smolensk", "kernel": "6.1.0", "scope": "full", "department_id": dept_a},
        )
        assert smolensk_resp.status_code == 200, smolensk_resp.text
        smolensk_body = smolensk_resp.json()
        assert smolensk_body["errors"] == []
        assert len(smolensk_body["test_runs"]) == 1

        async with AsyncSessionLocal() as db:
            orel_case = await stp_test_case_repo.get_by_code(db, orel_code)
            smolensk_case = await stp_test_case_repo.get_by_code(db, smolensk_code)
            orel_cells = await stp_cell_repo.list_by_run(db, orel_body["test_runs"][0]["id"])
            smolensk_cells = await stp_cell_repo.list_by_run(db, smolensk_body["test_runs"][0]["id"])
        assert {c.stp_test_case_id for c in orel_cells} == {orel_case.id}
        assert {c.stp_test_case_id for c in smolensk_cells} == {smolensk_case.id}

    async def test_guest_cannot_generate(self, client, guest_token, dept_a):
        resp = await client.post(
            f"{STP_BASE}/generate", headers=_hdr(guest_token),
            json={
                "os_version_id": "1.8.5.46", "mode": "orel", "kernel": "6.1.0",
                "scope": "full", "department_id": dept_a,
            },
        )
        assert resp.status_code == 403

    async def test_invalid_scope_rejected(self, client, admin_token, dept_a):
        resp = await client.post(
            f"{STP_BASE}/generate", headers=_hdr(admin_token),
            json={
                "os_version_id": "1.8.5.46", "mode": "orel", "kernel": "6.1.0",
                "scope": "bogus", "department_id": dept_a,
            },
        )
        assert resp.status_code == 422


# ── Событийное обновление stp_cells из очереди ──────────────────────────────


class TestStpEventDrivenStatus:
    async def test_success_updates_cell_and_pushes_to_zephyr(
        self, client, admin_token, mock_server_service, configure_internal_keys,
        mock_secret_client, dept_a, monkeypatch,
    ):
        mock_server_service()
        stand_id, _ = await _create_stand(client, admin_token, department_id=dept_a)
        test_id, code = await _create_test_def_for_dept(client, admin_token, stand_id, dept_a)
        case_id = await _seed_stp_test_case(code, zephyr_id="BT-T1")
        async with AsyncSessionLocal() as db:
            run = await stp_test_run_repo.create(db, {
                "id": stp_test_run_id(), "os_version_id": LAUNCH_CTX["RC"],
                "mode": LAUNCH_CTX["MODE"], "kernel": LAUNCH_CTX["KERNEL"],
                "stand_id": stand_id, "zephyr_test_run_key": "BT-R1",
                "zephyr_folder_path": "/stress_test/1.8/1.8.5",
            })
            cell = await stp_cell_repo.create(db, {
                "id": stp_cell_id(), "stp_test_case_id": case_id, "stp_test_run_id": run.id,
                "status": StpCellStatus.NOT_RUN,
            })
            await db.commit()
            run_id, cell_id = run.id, cell.id

        await _seed_integration_settings(dept_a, bitbucket_credential_id="cred_bitbucket")
        mock_secret_client["cred_x"] = ("jira_bot", "tok123")
        mock_secret_client["cred_bitbucket"] = ("git-bot", "git-token")

        pushed = []

        async def fake_update_result(*, base_url, bearer_token, test_run_key, test_case_key, status):
            pushed.append((test_run_key, test_case_key, status))

        monkeypatch.setattr(zephyr_client, "update_test_result", fake_update_result)

        async with AsyncSessionLocal() as db:
            item = await queue_svc.enqueue(db, _identity(department_id=dept_a), test_id, launch_context=LAUNCH_CTX)
        await client.post(
            f"{CALLBACK_BASE}/{item.prepare_request_id}/completed",
            headers=_server_hdr("server_service", SERVER_SECRET),
            json={
                "correlation_id": item.id, "succeeded": True,
                "test_username": "u", "test_password": "s3cr3t", "test_ssh_private_key": "-----KEY-----",
            },
        )
        await client.post(f"{QUEUE_BASE}/claim", headers=_server_hdr("testing_worker", WORKER_SECRET))
        resp = await client.post(
            f"{QUEUE_BASE}/{item.id}/completed",
            headers=_server_hdr("testing_worker", WORKER_SECRET),
            json={"succeeded": True, "exit_code": 0},
        )
        assert resp.status_code == 200, resp.text

        async with AsyncSessionLocal() as db:
            refreshed = await stp_cell_repo.get_by_id(db, cell_id)
        assert refreshed.status == StpCellStatus.PASSED
        assert refreshed.queue_item_id == item.id
        assert refreshed.updated_by is None
        assert pushed == [("BT-R1", "BT-T1", StpCellStatus.PASSED)]

    async def test_failure_updates_cell_to_fail(
        self, client, admin_token, mock_server_service, configure_internal_keys, dept_a,
    ):
        mock_server_service()
        stand_id, _ = await _create_stand(client, admin_token, department_id=dept_a)
        test_id, code = await _create_test_def_for_dept(client, admin_token, stand_id, dept_a)
        case_id = await _seed_stp_test_case(code, zephyr_id="BT-T1")
        # Настройки интеграции для dept_a намеренно НЕ заведены — путь "нет
        # креды" должен просто пропустить push в Zephyr, не падать.
        async with AsyncSessionLocal() as db:
            run = await stp_test_run_repo.create(db, {
                "id": stp_test_run_id(), "os_version_id": LAUNCH_CTX["RC"],
                "mode": LAUNCH_CTX["MODE"], "kernel": LAUNCH_CTX["KERNEL"],
                "stand_id": stand_id, "zephyr_test_run_key": "BT-R1",
                "zephyr_folder_path": "/stress_test/1.8/1.8.5",
            })
            cell = await stp_cell_repo.create(db, {
                "id": stp_cell_id(), "stp_test_case_id": case_id, "stp_test_run_id": run.id,
                "status": StpCellStatus.NOT_RUN,
            })
            await db.commit()
            cell_id = cell.id

        async with AsyncSessionLocal() as db:
            from src.repositories import department_test_settings as dts_repo
            from src.utils.ids import department_test_settings_id

            await dts_repo.create(db, {
                "id": department_test_settings_id(), "department_id": dept_a,
                "retry_enabled": False, "test_username": "u", "activity_report_auto_generate": False,
            })
            await db.commit()

        async with AsyncSessionLocal() as db:
            item = await queue_svc.enqueue(db, _identity(department_id=dept_a), test_id, launch_context=LAUNCH_CTX)
        await client.post(
            f"{CALLBACK_BASE}/{item.prepare_request_id}/completed",
            headers=_server_hdr("server_service", SERVER_SECRET),
            json={
                "correlation_id": item.id, "succeeded": True,
                "test_username": "u", "test_password": "s3cr3t", "test_ssh_private_key": "-----KEY-----",
            },
        )
        await client.post(f"{QUEUE_BASE}/claim", headers=_server_hdr("testing_worker", WORKER_SECRET))
        resp = await client.post(
            f"{QUEUE_BASE}/{item.id}/completed",
            headers=_server_hdr("testing_worker", WORKER_SECRET),
            json={"succeeded": False, "exit_code": 1, "error": "ssh timeout"},
        )
        assert resp.status_code == 200, resp.text

        async with AsyncSessionLocal() as db:
            refreshed = await stp_cell_repo.get_by_id(db, cell_id)
        assert refreshed.status == StpCellStatus.FAIL
        assert refreshed.queue_item_id == item.id

    async def test_no_stp_run_is_a_silent_noop(
        self, client, admin_token, mock_server_service, configure_internal_keys, dept_a,
    ):
        """Тест без связанного СТП-прогона — обычный случай, завершение не должно падать."""
        mock_server_service()
        stand_id, _ = await _create_stand(client, admin_token, department_id=dept_a)
        test_id, _code = await _create_test_def_for_dept(client, admin_token, stand_id, dept_a)
        async with AsyncSessionLocal() as db:
            item = await queue_svc.enqueue(db, _identity(department_id=dept_a), test_id, launch_context=LAUNCH_CTX)
        await client.post(
            f"{CALLBACK_BASE}/{item.prepare_request_id}/completed",
            headers=_server_hdr("server_service", SERVER_SECRET),
            json={
                "correlation_id": item.id, "succeeded": True,
                "test_username": "u", "test_password": "s3cr3t", "test_ssh_private_key": "-----KEY-----",
            },
        )
        await client.post(f"{QUEUE_BASE}/claim", headers=_server_hdr("testing_worker", WORKER_SECRET))
        resp = await client.post(
            f"{QUEUE_BASE}/{item.id}/completed",
            headers=_server_hdr("testing_worker", WORKER_SECRET),
            json={"succeeded": True, "exit_code": 0},
        )
        assert resp.status_code == 200, resp.text


# ── Ручной override ──────────────────────────────────────────────────────────


class TestStpCellManualOverride:
    async def _make_cell(self, client, admin_token, dept_a, mock_server_service) -> str:
        mock_server_service()
        stand_id, _ = await _create_stand(client, admin_token, department_id=dept_a)
        code = f"stp.case.{uuid.uuid4().hex[:8]}"
        case_id = await _seed_stp_test_case(code, zephyr_id="BT-T1")
        async with AsyncSessionLocal() as db:
            run = await stp_test_run_repo.create(db, {
                "id": stp_test_run_id(), "os_version_id": "1.8.5.46", "mode": "orel",
                "kernel": "6.1.0", "stand_id": stand_id, "zephyr_test_run_key": None,
                "zephyr_folder_path": None,
            })
            cell = await stp_cell_repo.create(db, {
                "id": stp_cell_id(), "stp_test_case_id": case_id, "stp_test_run_id": run.id,
                "status": StpCellStatus.NOT_RUN,
            })
            await db.commit()
            return cell.id

    async def test_admin_can_override(self, client, admin_token, dept_a, mock_server_service):
        cell_id = await self._make_cell(client, admin_token, dept_a, mock_server_service)
        resp = await client.patch(
            f"{STP_BASE}/cells/{cell_id}", headers=_hdr(admin_token), json={"status": "pass"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "pass"
        assert body["updated_by"] is not None
        assert body["queue_item_id"] is None

    async def test_guest_cannot_override(self, client, admin_token, guest_token, dept_a, mock_server_service):
        cell_id = await self._make_cell(client, admin_token, dept_a, mock_server_service)
        resp = await client.patch(
            f"{STP_BASE}/cells/{cell_id}", headers=_hdr(guest_token), json={"status": "pass"},
        )
        assert resp.status_code == 403

    async def test_invalid_status_rejected(self, client, admin_token, dept_a, mock_server_service):
        cell_id = await self._make_cell(client, admin_token, dept_a, mock_server_service)
        resp = await client.patch(
            f"{STP_BASE}/cells/{cell_id}", headers=_hdr(admin_token), json={"status": "bogus"},
        )
        assert resp.status_code == 422

    async def test_not_found(self, client, admin_token):
        resp = await client.patch(
            f"{STP_BASE}/cells/cell_does_not_exist", headers=_hdr(admin_token), json={"status": "pass"},
        )
        assert resp.status_code == 404
