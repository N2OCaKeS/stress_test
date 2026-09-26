"""Папка Zephyr: шаблоны пути/имени, поиск/создание, ручная правка, `FOLDER_TREE_ID`.

Zephyr замокан на уровне модульных функций `zephyr_client` (`zephyr_folder_api`
ниже) — ни одного сетевого вызова. Фикстуры `rc_release_variable` и
`zephyr_folder_api` переиспользуют `test_stp.py`/`test_stp_pull_from_life.py`/
`test_kernel_campaign_and_log_rotation.py`: шаблон пути по умолчанию ссылается
на `RC_RELEASE`, которую заводит.
"""

from __future__ import annotations

import json
import uuid

import httpx
import pytest
from sqlalchemy import create_engine, text

from src.core.constants import GlobalVariableSource
from src.core.exceptions import DomainValidationError, ServiceUnavailableError
from src.db.session import AsyncSessionLocal
from src.repositories import department_integration_settings as dis_repo
from src.repositories import zephyr_folder as zephyr_folder_repo
from src.services import server_client, variable_resolver, zephyr_client
from src.services import zephyr_folder as zephyr_folder_svc
from src.utils.ids import department_integration_settings_id, zephyr_folder_id
from src.models import GlobalVariable
from tests.conftest import TEST_DATABASE_URL
from tests.conftest import auth_hdr as _hdr
from tests.test_queue import mock_server_service, recorded_calls  # noqa: F401 — фикстуры по имени

STP_BASE = "/api/testing/v1/stp"
FOLDER_BASE = STP_BASE + "/zephyr-folder"
DIS_BASE = "/api/testing/v1/department-integration-settings"
VARS_BASE = "/api/testing/v1/global-variables"


# ── фикстуры (переиспользуются другими модулями) ─────────────────────────────


async def _fake_os_version_resolver(ctx, variable, _stack):
    """Резолв `os_version` по контракту (CONTRACTS.md C1) — только для тестов,
    пока не влит: карточка версии по `launch_context["RC"]`, имя с
    `segments`/`uu_segments` первыми сегментами."""
    ref = variable.source_ref or {}
    info = await server_client.resolve_os_version_info(str(ctx.launch_context["RC"]))
    field = ref.get("field")
    if field == "rc_number":
        return variable_resolver.Resolved(info.rc_number or "")
    if field == "is_urgent_update":
        return variable_resolver.Resolved("true" if info.is_urgent_update else "false")
    parts = info.name.split(".")
    count = ref.get("segments")
    if info.is_urgent_update and ref.get("uu_segments") and len(parts) == 6 and parts[3].upper() == "UU":
        count = ref["uu_segments"]
    return variable_resolver.Resolved(".".join(parts[:count]) if count else info.name)


@pytest.fixture
def rc_release_variable(monkeypatch):
    """`RC_RELEASE` для шаблона пути по умолчанию.

    Переменную и резолв сегментов версии заводит. Если её сид уже в
    базе — фикстура ничего не делает (работает настоящий резолвер). Иначе
    заводит переменную той же формы, что сид, и подменяет резолвер
    источника `os_version` его контрактом.
    """
    engine = create_engine(TEST_DATABASE_URL, isolation_level="AUTOCOMMIT", pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            seeded = conn.execute(text("SELECT 1 FROM global_variables WHERE code = 'RC_RELEASE'")).scalar()
            if seeded:
                return
            conn.execute(
                text(
                    "INSERT INTO global_variables (id, code, label, source, value_type, is_sensitive, "
                    "description, source_ref, created_by) VALUES ('gvar_rc_release_fixture', 'RC_RELEASE', "
                    "'Релиз версии ОС', 'os_version', 'string', false, 'RC_RELEASE test fixture', "
                    "CAST(:ref AS jsonb), 'usr_zephyr_folder_fixture')"
                ),
                {"ref": json.dumps({"field": "name", "segments": 3, "uu_segments": 5})},
            )
    finally:
        engine.dispose()
    monkeypatch.setitem(variable_resolver._RESOLVERS, GlobalVariableSource.OS_VERSION, _fake_os_version_resolver)


@pytest.fixture
def zephyr_folder_api(monkeypatch):
    """Дерево папок Zephyr в памяти.

    `existing` — папки, которые уже есть (`путь → id`): создание такой папки
    отказывает, как ATM на дубликат. `with_runs` — папки, в которых лежат
    test-run'ы: только по ним `find_test_run_folder_id` видит id.
    `refuse_create=True` — Zephyr не даёт заводить папки вовсе.
    """
    state: dict = {
        "existing": {}, "with_runs": {}, "refuse_create": False,
        "find": [], "create": [], "next_id": 5000,
    }

    async def fake_find(*, base_url, bearer_token, folder, project_key="BT"):
        state["find"].append(folder)
        return state["with_runs"].get(folder)

    async def fake_create(*, base_url, bearer_token, folder, project_key="BT"):
        state["create"].append(folder)
        if state["refuse_create"] or folder in state["existing"]:
            return None
        state["next_id"] += 1
        state["existing"][folder] = str(state["next_id"])
        return state["existing"][folder]

    monkeypatch.setattr(zephyr_client, "find_test_run_folder_id", fake_find)
    monkeypatch.setattr(zephyr_client, "create_test_run_folder", fake_create)
    return state


# ── хелперы ──────────────────────────────────────────────────────────────────


async def _seed_folder(department_id: str, os_version_id: str, *, folder_tree_id="4242",
                       folder_path="/stress_test/1.8.5/1.8.5.46", is_manual=False) -> None:
    async with AsyncSessionLocal() as db:
        await zephyr_folder_repo.create(db, {
            "id": zephyr_folder_id(), "department_id": department_id, "os_version_id": os_version_id,
            "folder_path": folder_path, "folder_tree_id": folder_tree_id, "is_manual": is_manual,
        })
        await db.commit()


async def _resolve(department_id: str | None, rc: str, code: str) -> str:
    async with AsyncSessionLocal() as db:
        ctx = variable_resolver.ResolveContext(
            db=db, department_id=department_id, test=None, stand=None, launch_context={"RC": rc},
        )
        return (await variable_resolver.resolve_code(ctx, code)).value


@pytest.fixture
def os_versions(monkeypatch):
    """`{os_version_id: {"name", "is_urgent_update"}}` для `server_client.get_os_version`."""
    catalog: dict[str, dict] = {}

    async def fake_get_os_version(os_version_id: str) -> dict:
        return {"id": os_version_id, "name": os_version_id, **catalog.get(os_version_id, {})}

    monkeypatch.setattr(server_client, "get_os_version", fake_get_os_version)
    return catalog


# ── источник `zephyr_folder` в резолвере ─────────────────────────────────────


class TestZephyrFolderSource:
    async def test_folder_tree_id_seed_uses_zephyr_folder_source(self, client, admin_token):
        resp = await client.get(f"{VARS_BASE}/by-code/FOLDER_TREE_ID", headers=_hdr(admin_token))
        assert resp.status_code == 200, resp.text
        assert resp.json()["source"] == "zephyr_folder"
        assert resp.json()["source_ref"] == {"field": "folder_tree_id"}

    async def test_resolves_folder_tree_id_and_path_by_department_and_os_version(self, dept_a):
        await _seed_folder(dept_a, "osv_1", folder_tree_id="2967", folder_path="/stress_test/1.8.5/1.8.5.46")
        assert await _resolve(dept_a, "osv_1", "FOLDER_TREE_ID") == "2967"

        async with AsyncSessionLocal() as db:
            ctx = variable_resolver.ResolveContext(
                db=db, department_id=dept_a, test=None, stand=None, launch_context={"RC": "osv_1"},
            )
            var = GlobalVariable(
                id="gv_x", code="ZF_PATH", label="x", source="zephyr_folder",
                source_ref={"field": "folder_path"}, value_type="string", is_sensitive=False,
            )
            assert (await variable_resolver.resolve_variable(ctx, var)).value == "/stress_test/1.8.5/1.8.5.46"

    async def test_other_department_does_not_see_the_record(self, dept_a):
        await _seed_folder(dept_a, "osv_2")
        with pytest.raises(DomainValidationError) as err:
            await _resolve("dep_other", "osv_2", "FOLDER_TREE_ID")
        assert err.value.error_code == "VARIABLE_VALUE_MISSING"

    async def test_missing_record_is_an_error_with_hint(self, dept_a):
        with pytest.raises(DomainValidationError) as err:
            await _resolve(dept_a, "osv_missing", "FOLDER_TREE_ID")
        assert err.value.error_code == "VARIABLE_VALUE_MISSING"
        assert "сгенерируйте СТП" in err.value.details["hint"]

    async def test_record_without_id_is_an_error(self, dept_a):
        await _seed_folder(dept_a, "osv_3", folder_tree_id=None)
        with pytest.raises(DomainValidationError) as err:
            await _resolve(dept_a, "osv_3", "FOLDER_TREE_ID")
        assert err.value.error_code == "VARIABLE_VALUE_MISSING"

    async def test_needs_department(self):
        with pytest.raises(DomainValidationError) as err:
            await _resolve(None, "osv_4", "FOLDER_TREE_ID")
        assert err.value.error_code == "VARIABLE_CONTEXT_MISSING"

    @pytest.mark.parametrize("ref", [None, {}, {"field": "bogus"}, {"field": "folder_tree_id", "x": 1}])
    def test_source_ref_validation(self, ref):
        with pytest.raises(DomainValidationError) as err:
            variable_resolver.validate_source_ref("zephyr_folder", ref, is_sensitive=False)
        assert err.value.error_code == "VARIABLE_SOURCE_REF_INVALID"

    def test_valid_source_ref(self):
        ref = {"field": "folder_path"}
        assert variable_resolver.validate_source_ref("zephyr_folder", ref, is_sensitive=False) == ref

    async def test_variable_with_zephyr_folder_source_can_be_created(self, client, admin_token):
        resp = await client.post(
            VARS_BASE, headers=_hdr(admin_token),
            json={
                "code": f"ZF_{uuid.uuid4().hex[:6].upper()}", "label": "Путь папки",
                "source": "zephyr_folder", "source_ref": {"field": "folder_path"},
            },
        )
        assert resp.status_code == 201, resp.text


# ── шаблоны пути и имени ─────────────────────────────────────────────────────


class TestTemplates:
    async def test_default_path_is_legacy_format(self, rc_release_variable, os_versions, dept_a):
        async with AsyncSessionLocal() as db:
            path = await zephyr_folder_svc.render_folder_path(db, department_id=dept_a, os_version_id="1.8.5.46")
        assert path == "/stress_test/1.8.5/1.8.5.46"

    async def test_default_path_for_urgent_update(self, rc_release_variable, os_versions, dept_a):
        os_versions["osv_uu"] = {"name": "1.7.3.UU.1.2", "is_urgent_update": True}
        async with AsyncSessionLocal() as db:
            path = await zephyr_folder_svc.render_folder_path(db, department_id=dept_a, os_version_id="osv_uu")
        assert path == "/stress_test/1.7.3.UU.1/1.7.3.UU.1.2"

    async def test_department_template_and_normalization(self, os_versions, dept_a):
        async with AsyncSessionLocal() as db:
            await dis_repo.create(db, {
                "id": department_integration_settings_id(), "department_id": dept_a,
                "zephyr_folder_path_template": "custom//{RC_NAME}/",
            })
            await db.commit()
            path = await zephyr_folder_svc.render_folder_path(db, department_id=dept_a, os_version_id="1.9.0.1")
        assert path == "/custom/1.9.0.1"

    async def test_path_template_cannot_use_stand_variables(self, os_versions, dept_a):
        async with AsyncSessionLocal() as db:
            await dis_repo.create(db, {
                "id": department_integration_settings_id(), "department_id": dept_a,
                "zephyr_folder_path_template": "/x/{STAND_TOKEN}",
            })
            await db.commit()
            with pytest.raises(DomainValidationError) as err:
                await zephyr_folder_svc.render_folder_path(db, department_id=dept_a, os_version_id="1.9.0.1")
        assert err.value.error_code == "VARIABLE_CONTEXT_MISSING"
        assert err.value.details["template_field"] == "zephyr_folder_path_template"


# ── генерация СТП ────────────────────────────────────────────────────────────


@pytest.fixture
def stp_env(rc_release_variable, zephyr_folder_api, mock_server_service, monkeypatch, os_versions):
    """Всё, что нужно `/stp/generate`: креды, Zephyr-раны, папки."""
    from src.services import secret_client

    runs: list[dict] = []

    async def fake_create_test_run(*, base_url, bearer_token, folder, name, items, project_key="BT"):
        runs.append({"folder": folder, "name": name})
        # Прогон лёг в папку — теперь её id виден через поиск прогонов.
        if folder in zephyr_folder_api["existing"]:
            zephyr_folder_api["with_runs"][folder] = zephyr_folder_api["existing"][folder]
        return f"BT-R{len(runs)}"

    async def fake_resolve_user_key(*, base_url, bearer_token, username):
        return None

    async def fake_reveal(cred_id: str):
        return ("jira_bot", "tok123")

    monkeypatch.setattr(zephyr_client, "create_test_run", fake_create_test_run)
    monkeypatch.setattr(zephyr_client, "resolve_user_key", fake_resolve_user_key)
    monkeypatch.setattr(secret_client, "reveal_credential", fake_reveal)
    mock_server_service()
    return {"runs": runs, "folders": zephyr_folder_api}


async def _prepare_stand_with_test(client, admin_token, dept_a, *, legacy_token="stand3"):
    from tests.test_queue import _create_stand
    from tests.test_stp import _create_test_def_for_dept, _seed_integration_settings, _seed_stp_test_case

    stand_id, _ = await _create_stand(client, admin_token, department_id=dept_a, legacy_token=legacy_token)
    _test_id, code = await _create_test_def_for_dept(client, admin_token, stand_id, dept_a)
    await _seed_stp_test_case(code, zephyr_id="BT-T1")
    await _seed_integration_settings(dept_a)
    return stand_id


async def _generate(client, admin_token, dept_a, rc: str):
    return await client.post(
        f"{STP_BASE}/generate", headers=_hdr(admin_token),
        json={"os_version_id": rc, "mode": "orel", "kernel": "6.1.0", "scope": "full", "department_id": dept_a},
    )


class TestGenerateFindsOrCreatesFolder:
    async def test_missing_folder_is_created_and_saved(self, client, admin_token, dept_a, stp_env):
        await _prepare_stand_with_test(client, admin_token, dept_a)
        stp_env["folders"]["existing"]["/stress_test"] = "2744"

        resp = await _generate(client, admin_token, dept_a, "1.8.5.46")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["errors"] == []
        folder = body["zephyr_folder"]
        assert folder["folder_path"] == "/stress_test/1.8.5/1.8.5.46"
        assert folder["folder_tree_id"] == stp_env["folders"]["existing"]["/stress_test/1.8.5/1.8.5.46"]
        assert folder["is_manual"] is False
        assert folder["error"] is None
        # Родитель — раньше дочерней, как в легаси `add_testrun_folder`.
        assert stp_env["folders"]["create"] == [
            "/stress_test", "/stress_test/1.8.5", "/stress_test/1.8.5/1.8.5.46",
        ]
        assert stp_env["runs"] == [{"folder": "/stress_test/1.8.5/1.8.5.46", "name": "1.8.5.46_orel_6.1.0_stand3"}]
        assert await _resolve(dept_a, "1.8.5.46", "FOLDER_TREE_ID") == folder["folder_tree_id"]

    async def test_folder_found_through_its_runs(self, client, admin_token, dept_a, stp_env):
        await _prepare_stand_with_test(client, admin_token, dept_a)
        stp_env["folders"]["with_runs"]["/stress_test/1.8.5/1.8.5.46"] = "2967"

        resp = await _generate(client, admin_token, dept_a, "1.8.5.46")
        assert resp.status_code == 200, resp.text
        assert resp.json()["zephyr_folder"]["folder_tree_id"] == "2967"
        assert stp_env["folders"]["create"] == []
        assert await _resolve(dept_a, "1.8.5.46", "FOLDER_TREE_ID") == "2967"

    async def test_existing_empty_folder_is_found_after_runs_are_created(
        self, client, admin_token, dept_a, stp_env,
    ):
        """Папка уже есть, но без прогонов: создать нельзя («уже есть»), найти не
        по чему — id находится повторным поиском, когда в ней легли наши раны."""
        await _prepare_stand_with_test(client, admin_token, dept_a)
        stp_env["folders"]["existing"].update({
            "/stress_test": "2744", "/stress_test/1.8.5": "2773", "/stress_test/1.8.5/1.8.5.46": "2990",
        })

        resp = await _generate(client, admin_token, dept_a, "1.8.5.46")
        assert resp.status_code == 200, resp.text
        folder = resp.json()["zephyr_folder"]
        assert folder["folder_tree_id"] == "2990"
        assert folder["error"] is None
        assert stp_env["folders"]["find"] == ["/stress_test/1.8.5/1.8.5.46"] * 2

    async def test_not_found_and_not_created_is_a_soft_error(self, client, admin_token, dept_a, stp_env):
        await _prepare_stand_with_test(client, admin_token, dept_a)
        stp_env["folders"]["refuse_create"] = True

        resp = await _generate(client, admin_token, dept_a, "1.8.5.46")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # Прогон всё равно заведён — id папки нужен скриптам, не созданию рана.
        assert len(body["test_runs"]) == 1
        assert body["zephyr_folder"]["folder_tree_id"] is None
        assert body["zephyr_folder"]["error"]["error_code"] == "ZEPHYR_FOLDER_NOT_FOUND"
        async with AsyncSessionLocal() as db:
            record = await zephyr_folder_repo.get_by_department_and_os_version(db, dept_a, "1.8.5.46")
        assert record.folder_path == "/stress_test/1.8.5/1.8.5.46"
        assert record.folder_tree_id is None
        with pytest.raises(DomainValidationError):
            await _resolve(dept_a, "1.8.5.46", "FOLDER_TREE_ID")

    async def test_saved_id_is_reused_without_zephyr_calls(self, client, admin_token, dept_a, stp_env):
        await _prepare_stand_with_test(client, admin_token, dept_a)
        stp_env["folders"]["with_runs"]["/stress_test/1.8.5/1.8.5.46"] = "2967"
        assert (await _generate(client, admin_token, dept_a, "1.8.5.46")).status_code == 200
        stp_env["folders"]["find"].clear()

        resp = await _generate(client, admin_token, dept_a, "1.8.5.46")
        assert resp.status_code == 200, resp.text
        assert resp.json()["zephyr_folder"]["folder_tree_id"] == "2967"
        assert stp_env["folders"]["find"] == []

    async def test_manual_record_survives_regeneration(self, client, admin_token, dept_a, stp_env):
        await _prepare_stand_with_test(client, admin_token, dept_a)
        put = await client.put(
            FOLDER_BASE, headers=_hdr(admin_token),
            json={
                "os_version_id": "1.8.5.46", "department_id": dept_a,
                "folder_tree_id": "777", "folder_path": "/manual/1.8.5.46",
            },
        )
        assert put.status_code == 200, put.text
        assert put.json()["is_manual"] is True

        for _ in range(2):
            resp = await _generate(client, admin_token, dept_a, "1.8.5.46")
            assert resp.status_code == 200, resp.text
            folder = resp.json()["zephyr_folder"]
            assert (folder["folder_tree_id"], folder["is_manual"]) == ("777", True)

        assert stp_env["folders"]["find"] == []
        assert stp_env["folders"]["create"] == []
        # Прогоны — в папку ручной записи, иначе скрипт по её id их не найдёт.
        assert {r["folder"] for r in stp_env["runs"]} == {"/manual/1.8.5.46"}
        assert await _resolve(dept_a, "1.8.5.46", "FOLDER_TREE_ID") == "777"

    async def test_department_templates_drive_folder_and_run_name(self, client, admin_token, dept_a, stp_env):
        await _prepare_stand_with_test(client, admin_token, dept_a)
        async with AsyncSessionLocal() as db:
            row = await dis_repo.get_by_department(db, dept_a)
            await dis_repo.update(db, row, {
                "zephyr_folder_path_template": "/qa/{RC_NAME}",
                "zephyr_run_name_template": "{RC_NAME}-{MODE}-{STAND_TOKEN}",
            })
            await db.commit()

        resp = await _generate(client, admin_token, dept_a, "1.9.0.1")
        assert resp.status_code == 200, resp.text
        assert stp_env["runs"] == [{"folder": "/qa/1.9.0.1", "name": "1.9.0.1-orel-stand3"}]
        assert resp.json()["test_runs"][0]["zephyr_folder_path"] == "/qa/1.9.0.1"

    async def test_unresolvable_path_template_is_422_before_any_write(
        self, client, admin_token, dept_a, stp_env,
    ):
        await _prepare_stand_with_test(client, admin_token, dept_a)
        async with AsyncSessionLocal() as db:
            row = await dis_repo.get_by_department(db, dept_a)
            await dis_repo.update(db, row, {"zephyr_folder_path_template": "/x/{NO_SUCH_VARIABLE}"})
            await db.commit()

        rc = f"1.8.5.{uuid.uuid4().hex[:6]}"  # stp_compositions между тестами не чистятся
        resp = await _generate(client, admin_token, dept_a, rc)
        assert resp.status_code == 422, resp.text
        assert resp.json()["error_code"] == "VARIABLE_TEMPLATE_UNKNOWN"
        assert stp_env["runs"] == []
        comp = await client.get(
            f"{STP_BASE}/composition", headers=_hdr(admin_token),
            params={"os_version_id": rc, "department_id": dept_a},
        )
        assert comp.json()["revision"] == 0

    async def test_broken_run_name_template_is_a_per_stand_error(self, client, admin_token, dept_a, stp_env):
        stand_id = await _prepare_stand_with_test(client, admin_token, dept_a)
        async with AsyncSessionLocal() as db:
            row = await dis_repo.get_by_department(db, dept_a)
            await dis_repo.update(db, row, {"zephyr_run_name_template": "{RC_NAME}_{NO_SUCH_VARIABLE}"})
            await db.commit()

        resp = await _generate(client, admin_token, dept_a, "1.8.5.46")
        assert resp.status_code == 200, resp.text
        errors = resp.json()["errors"]
        assert [(e["stand_id"], e["error_code"]) for e in errors] == [(stand_id, "VARIABLE_TEMPLATE_UNKNOWN")]


# ── /stp/zephyr-folder ───────────────────────────────────────────────────────


class TestZephyrFolderApi:
    async def test_get_without_record_shows_template_path(
        self, client, guest_token, dept_a, rc_release_variable, os_versions,
    ):
        resp = await client.get(FOLDER_BASE, headers=_hdr(guest_token), params={"os_version_id": "1.8.5.46"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["id"] is None
        assert body["folder_path"] == "/stress_test/1.8.5/1.8.5.46"
        assert body["folder_tree_id"] is None
        assert body["error"] is None

    async def test_get_reports_unresolvable_template(self, client, guest_token, dept_a, os_versions):
        async with AsyncSessionLocal() as db:
            await dis_repo.create(db, {
                "id": department_integration_settings_id(), "department_id": dept_a,
                "zephyr_folder_path_template": "/x/{NO_SUCH_VARIABLE}",
            })
            await db.commit()
        resp = await client.get(FOLDER_BASE, headers=_hdr(guest_token), params={"os_version_id": "1.8.5.46"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["folder_path"] is None
        assert resp.json()["error"]["error_code"] == "VARIABLE_TEMPLATE_UNKNOWN"

    async def test_foreign_department_is_isolated(self, client, guest_token):
        resp = await client.get(
            FOLDER_BASE, headers=_hdr(guest_token), params={"os_version_id": "x", "department_id": "dep_b"},
        )
        assert resp.status_code == 403

    async def test_guest_cannot_set_or_refresh(self, client, guest_token, dept_a):
        put = await client.put(
            FOLDER_BASE, headers=_hdr(guest_token),
            json={"os_version_id": "1.8.5.46", "folder_tree_id": "1"},
        )
        assert put.status_code == 403
        post = await client.post(f"{FOLDER_BASE}/refresh", headers=_hdr(guest_token), json={"os_version_id": "1.8.5.46"})
        assert post.status_code == 403

    async def test_manual_set_keeps_path_of_existing_record(self, client, admin_token, dept_a):
        await _seed_folder(dept_a, "osv_m", folder_tree_id=None, folder_path="/stress_test/1.8/1.8.1")
        resp = await client.put(
            FOLDER_BASE, headers=_hdr(admin_token),
            json={"os_version_id": "osv_m", "folder_tree_id": " 31337 "},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert (body["folder_path"], body["folder_tree_id"], body["is_manual"]) == ("/stress_test/1.8/1.8.1", "31337", True)
        assert body["updated_by"] is not None
        assert body["resolved_at"] is not None

    async def test_blank_manual_id_rejected(self, client, admin_token, dept_a):
        resp = await client.put(
            FOLDER_BASE, headers=_hdr(admin_token), json={"os_version_id": "osv_m", "folder_tree_id": "  "},
        )
        assert resp.status_code == 422
        assert resp.json()["error_code"] == "ZEPHYR_FOLDER_ID_REQUIRED"

    async def test_refresh_drops_manual_flag_and_finds_again(
        self, client, admin_token, dept_a, stp_env,
    ):
        from tests.test_stp import _seed_integration_settings

        await _seed_integration_settings(dept_a)
        await _seed_folder(dept_a, "1.8.5.46", folder_tree_id="777", folder_path="/manual", is_manual=True)
        stp_env["folders"]["with_runs"]["/stress_test/1.8.5/1.8.5.46"] = "2967"

        resp = await client.post(
            f"{FOLDER_BASE}/refresh", headers=_hdr(admin_token), json={"os_version_id": "1.8.5.46"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert (body["folder_path"], body["folder_tree_id"], body["is_manual"]) == (
            "/stress_test/1.8.5/1.8.5.46", "2967", False,
        )
        assert body["error"] is None

    async def test_refresh_without_integration_reports_error(
        self, client, admin_token, dept_a, rc_release_variable, zephyr_folder_api, os_versions,
    ):
        resp = await client.post(
            f"{FOLDER_BASE}/refresh", headers=_hdr(admin_token), json={"os_version_id": "1.8.5.46"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["error"]["error_code"] == "JIRA_INTEGRATION_NOT_AVAILABLE"
        assert zephyr_folder_api["find"] == []

    async def test_refresh_keeps_record_when_zephyr_is_down(
        self, client, admin_token, dept_a, stp_env, monkeypatch,
    ):
        from tests.test_stp import _seed_integration_settings

        await _seed_integration_settings(dept_a)
        await _seed_folder(dept_a, "1.8.5.46", folder_tree_id="777", folder_path="/manual", is_manual=True)

        async def down(**_kwargs):
            raise ServiceUnavailableError(error_code="ZEPHYR_UNREACHABLE", message="down")

        monkeypatch.setattr(zephyr_client, "find_test_run_folder_id", down)
        resp = await client.post(
            f"{FOLDER_BASE}/refresh", headers=_hdr(admin_token), json={"os_version_id": "1.8.5.46"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["error"]["error_code"] == "ZEPHYR_UNREACHABLE"
        assert (body["folder_tree_id"], body["is_manual"]) == ("777", True)


# ── настройки интеграций отдела: шаблоны ─────────────────────────────────────


class TestIntegrationSettingsTemplates:
    async def test_defaults_without_row(self, client, guest_token, dept_a):
        body = (await client.get(f"{DIS_BASE}/{dept_a}", headers=_hdr(guest_token))).json()
        assert body["zephyr_folder_path_template"] == "/stress_test/{RC_RELEASE}/{RC_NAME}"
        assert body["zephyr_run_name_template"] == "{RC_NAME}_{MODE}_{KERNEL}_{STAND_TOKEN}"

    async def test_defaults_on_created_row(self, client, admin_token, dept_a):
        resp = await client.put(f"{DIS_BASE}/{dept_a}", headers=_hdr(admin_token), json={"jira_board_id": "1"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["zephyr_folder_path_template"] == "/stress_test/{RC_RELEASE}/{RC_NAME}"
        assert resp.json()["zephyr_run_name_template"] == "{RC_NAME}_{MODE}_{KERNEL}_{STAND_TOKEN}"

    async def test_custom_templates_roundtrip_and_null_resets(self, client, admin_token, dept_a):
        resp = await client.put(
            f"{DIS_BASE}/{dept_a}", headers=_hdr(admin_token),
            json={"zephyr_folder_path_template": " /qa/{RC_NAME} ", "zephyr_run_name_template": "{RC_NAME}_{MODE}"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["zephyr_folder_path_template"] == "/qa/{RC_NAME}"
        assert resp.json()["zephyr_run_name_template"] == "{RC_NAME}_{MODE}"

        reset = await client.put(
            f"{DIS_BASE}/{dept_a}", headers=_hdr(admin_token),
            json={"zephyr_folder_path_template": None, "zephyr_run_name_template": ""},
        )
        assert reset.status_code == 200, reset.text
        assert reset.json()["zephyr_folder_path_template"] == "/stress_test/{RC_RELEASE}/{RC_NAME}"
        assert reset.json()["zephyr_run_name_template"] == "{RC_NAME}_{MODE}_{KERNEL}_{STAND_TOKEN}"

    async def test_unchanged_default_is_not_revalidated(self, client, admin_token, dept_a):
        """Форма UI шлёт все поля: неизменный дефолт (с `RC_RELEASE`, которой до
         может не быть) не должен блокировать сохранение."""
        resp = await client.put(
            f"{DIS_BASE}/{dept_a}", headers=_hdr(admin_token),
            json={"jira_board_id": "7", "zephyr_folder_path_template": "/stress_test/{RC_RELEASE}/{RC_NAME}"},
        )
        assert resp.status_code == 200, resp.text

    async def test_unknown_variable_rejected(self, client, admin_token, dept_a):
        resp = await client.put(
            f"{DIS_BASE}/{dept_a}", headers=_hdr(admin_token),
            json={"zephyr_run_name_template": "{RC_NAME}_{NO_SUCH_VARIABLE}"},
        )
        assert resp.status_code == 422, resp.text
        assert resp.json()["error_code"] == "VARIABLE_TEMPLATE_UNKNOWN"

    async def test_relative_folder_path_rejected(self, client, admin_token, dept_a):
        resp = await client.put(
            f"{DIS_BASE}/{dept_a}", headers=_hdr(admin_token),
            json={"zephyr_folder_path_template": "stress_test/{RC_NAME}"},
        )
        assert resp.status_code == 422, resp.text
        assert resp.json()["error_code"] == "ZEPHYR_TEMPLATE_INVALID"


# ── zephyr_client: папки ─────────────────────────────────────────────────────


def _mock_http(monkeypatch, handler):
    settings_stub = type("S", (), {"zephyr_request_timeout_seconds": 2.0})()
    monkeypatch.setattr(zephyr_client, "get_settings", lambda: settings_stub)
    monkeypatch.setattr(
        zephyr_client, "build_client",
        lambda timeout: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


class TestZephyrClientFolders:
    async def test_create_folder_posts_legacy_body_and_returns_id(self, monkeypatch):
        seen: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/rest/atm/1.0/folder"
            seen.append(json.loads(request.content))
            return httpx.Response(201, json={"id": 2990})

        _mock_http(monkeypatch, handler)
        folder_id = await zephyr_client.create_test_run_folder(
            base_url="http://jira.example/", bearer_token="tok", folder="/stress_test/1.8.5/1.8.5.46",
        )
        assert folder_id == "2990"
        assert seen == [{"projectKey": "BT", "name": "/stress_test/1.8.5/1.8.5.46", "type": "TEST_RUN"}]

    async def test_create_folder_refused_returns_none(self, monkeypatch):
        _mock_http(monkeypatch, lambda request: httpx.Response(400, json={"message": "exists"}))
        assert await zephyr_client.create_test_run_folder(
            base_url="http://jira.example", bearer_token="tok", folder="/x",
        ) is None

    async def test_create_folder_server_error_raises(self, monkeypatch):
        _mock_http(monkeypatch, lambda request: httpx.Response(500))
        with pytest.raises(ServiceUnavailableError) as err:
            await zephyr_client.create_test_run_folder(base_url="http://jira.example", bearer_token="tok", folder="/x")
        assert err.value.error_code == "ZEPHYR_CREATE_FOLDER_FAILED"

    async def test_search_parses_folder_id_shapes(self, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"results": [
                {"key": "BT-C1", "name": "a", "folder": {"id": 2967, "name": "1.8.5.46"}},
                {"key": "BT-C2", "name": "b", "folder": "/stress_test/1.8.5/1.8.5.46", "folderId": 2968},
                {"key": "BT-C3", "name": "c", "folder": "/stress_test/1.8.5/1.8.5.46"},
            ]})

        _mock_http(monkeypatch, handler)
        runs = await zephyr_client.search_test_runs(base_url="http://jira.example", bearer_token="tok", folder="/f")
        assert [(r.key, r.folder, r.folder_id) for r in runs] == [
            ("BT-C1", "1.8.5.46", "2967"),
            ("BT-C2", "/stress_test/1.8.5/1.8.5.46", "2968"),
            ("BT-C3", "/stress_test/1.8.5/1.8.5.46", None),
        ]

    async def test_find_folder_id_skips_runs_of_other_folders(self, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=[
                {"key": "BT-C1", "name": "a", "folder": "/stress_test/1.8.5/1.8.5.46/sub", "folderId": 1},
                {"key": "BT-C2", "name": "b", "folder": "/stress_test/1.8.5/1.8.5.46"},
                {"key": "BT-C3", "name": "c", "folder": "/stress_test/1.8.5/1.8.5.46/", "folderId": 3},
            ])

        _mock_http(monkeypatch, handler)
        assert await zephyr_client.find_test_run_folder_id(
            base_url="http://jira.example", bearer_token="tok", folder="/stress_test/1.8.5/1.8.5.46",
        ) == "3"

    async def test_find_folder_id_none_without_runs(self, monkeypatch):
        _mock_http(monkeypatch, lambda request: httpx.Response(200, json=[]))
        assert await zephyr_client.find_test_run_folder_id(
            base_url="http://jira.example", bearer_token="tok", folder="/x",
        ) is None
