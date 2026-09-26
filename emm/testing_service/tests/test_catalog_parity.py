"""Поля теста `short_name`/`dates_quoting`, экранирование `dates.conf`,
перевод каталога allta_app на учётные данные отдела и `postgresql.smolensk`.

* `join_dates_tokens` — три режима D4; в режиме `shell` `shlex.split`
  возвращает ровно список токенов;
* паритет `dates` с легаси для всех 62 тестов — `test_legacy_golden_parity.py`
  здесь раньше был
  его частный случай для `postgresql.base`/`postgresql.smolensk`;
* миграция `catalog_parity_data` — переводит старый импорт и не трогает
  слоты, которые пользователь уже поменял; повторный прогон ничего не меняет;
* повторный импорт каталога идемпотентен.
"""

from __future__ import annotations

import importlib.util
import os
import shlex
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
import yaml
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import bindparam, create_engine, text

from scripts.import_catalog import run as import_run
from src.core.exceptions import DomainValidationError
from src.db.session import AsyncSessionLocal
from src.models import GlobalVariable, TestStand
from src.repositories import department_integration_settings as dis_repo
from src.repositories import test_command_arg as arg_repo
from src.repositories import test_definition as test_definition_repo
from src.services import secret_client
from src.services import test_command_arg as svc
from src.services import variable_resolver as vr
from tests.conftest import TEST_DATABASE_URL
from tests.conftest import auth_hdr as _hdr
from tests.legacy_parity import legacy
from tests.test_launch_context import (  # noqa: F401 — фикстуры каталога
    ALLTA_CATALOG,
    CATALOG_CTX,
    imported_allta_catalog,
    integration_for_catalog,
    mock_server_service,
    os_version_catalog,
    zephyr_folder_for_catalog,
)

SERVICE_DIR = Path(__file__).resolve().parents[1]
DATA_MIGRATION = SERVICE_DIR / "src" / "db" / "migrations" / "versions" / "f8b3d1e6a254_catalog_parity_data.py"
SCHEMA_REVISION = "e4a7c2d9b813"

TESTS_BASE = "/api/testing/v1/test-definitions"
VARS_BASE = "/api/testing/v1/global-variables"


# ── экранирование ────────────────────────────────────────────────────────────

class TestJoinDatesTokens:
    TOKENS = ["-tcas", "file system benchmark. XFS", "-ba", "a'b \"c\" $HOME", "", "-sn", "3"]

    def test_shell_round_trips_through_shlex(self):
        content = svc.join_dates_tokens(self.TOKENS, "shell")
        assert shlex.split(content) == self.TOKENS
        assert svc.join_dates_tokens(["-sn", "3"], "shell") == "-sn 3"

    def test_default_is_shell(self):
        assert svc.join_dates_tokens(self.TOKENS, None) == svc.join_dates_tokens(self.TOKENS, "shell")

    def test_legacy_quotes_only_tokens_with_spaces(self):
        content = svc.join_dates_tokens(["-tcas", "file system benchmark. XFS", "-sn", "3"], "legacy")
        assert content == '-tcas "file system benchmark. XFS" -sn 3'

    def test_raw_is_previous_behaviour(self):
        content = svc.join_dates_tokens(["-tcas", "file system benchmark. XFS"], "raw")
        assert content == "-tcas file system benchmark. XFS"

    def test_mask_is_quoted_like_any_token(self):
        assert svc.join_dates_tokens(["-ba", vr.MASK], "shell") == "-ba '***'"
        assert svc.join_dates_tokens(["-ba", vr.MASK], "legacy") == "-ba ***"


async def _create_test(client, token, **fields) -> dict:
    payload = {"code": f"sample.{uuid.uuid4().hex[:8]}", "full_name": "Тест образец", **fields}
    resp = await client.post(TESTS_BASE, headers=_hdr(token), json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


class TestTestDefinitionFields:
    async def test_defaults(self, client, admin_token):
        body = await _create_test(client, admin_token)
        assert body["short_name"] is None
        assert body["dates_quoting"] == "shell"

    async def test_create_and_patch(self, client, admin_token):
        body = await _create_test(client, admin_token, short_name="XFS", dates_quoting="legacy")
        assert (body["short_name"], body["dates_quoting"]) == ("XFS", "legacy")
        resp = await client.patch(
            f"{TESTS_BASE}/{body['id']}", headers=_hdr(admin_token),
            json={"short_name": "EXT4", "dates_quoting": "raw"},
        )
        assert resp.status_code == 200, resp.text
        assert (resp.json()["short_name"], resp.json()["dates_quoting"]) == ("EXT4", "raw")
        got = await client.get(f"{TESTS_BASE}/{body['id']}", headers=_hdr(admin_token))
        assert got.json()["dates_quoting"] == "raw"

    @pytest.mark.parametrize("value", [None, "bash"])
    async def test_invalid_dates_quoting_is_422(self, client, admin_token, value):
        body = await _create_test(client, admin_token)
        resp = await client.patch(
            f"{TESTS_BASE}/{body['id']}", headers=_hdr(admin_token), json={"dates_quoting": value},
        )
        assert resp.status_code == 422, resp.text

    async def test_short_name_too_long_is_422(self, client, admin_token):
        resp = await client.post(
            TESTS_BASE, headers=_hdr(admin_token),
            json={"code": f"sample.{uuid.uuid4().hex[:8]}", "full_name": "x", "short_name": "x" * 65},
        )
        assert resp.status_code == 422

    @pytest.mark.parametrize(("mode", "expected", "expected_masked"), [
        ("shell", "-tcas 'Тест образец' -p 'hunter 2'", "-tcas 'Тест образец' -p '***'"),
        ("legacy", '-tcas "Тест образец" -p "hunter 2"', '-tcas "Тест образец" -p ***'),
        ("raw", "-tcas Тест образец -p hunter 2", "-tcas Тест образец -p ***"),
    ])
    async def test_resolve_dates_uses_test_mode(self, client, admin_token, mode, expected, expected_masked):
        body = await _create_test(client, admin_token, dates_quoting=mode)
        case = await client.get(f"{VARS_BASE}/by-code/TEST_CASE_NAME", headers=_hdr(admin_token))
        password = await client.get(f"{VARS_BASE}/by-code/TEST_SSH_KEY", headers=_hdr(admin_token))
        for slot in (
            {"kind": "literal", "literal_value": "-tcas"},
            {"kind": "variable", "variable_id": case.json()["id"]},
            {"kind": "literal", "literal_value": "-p"},
            {"kind": "variable", "variable_id": password.json()["id"]},
        ):
            resp = await client.post(f"{TESTS_BASE}/{body['id']}/args", headers=_hdr(admin_token), json=slot)
            assert resp.status_code == 201, resp.text
        async with AsyncSessionLocal() as db:
            content, masked = await svc.resolve_dates(db, body["id"], {"TEST_SSH_KEY": "hunter 2"})
        assert (content, masked) == (expected, expected_masked)


# ── переменные интеграций отдела ─────────────────────────────────────────────

SEEDED = {
    "CONFLUENCE_USER": (
        {"field": "confluence_credential_id", "fallback": "credential_id", "credential_part": "login"}, False,
    ),
    "CONFLUENCE_TOKEN": (
        {"field": "confluence_credential_id", "fallback": "credential_id", "credential_part": "secret"}, True,
    ),
    "JIRA_BASIC_AUTH": ({"field": "credential_id", "credential_part": "secret"}, True),
    "CONFLUENCE_SPACE": ({"field": "stp_matrix_confluence_space"}, False),
}


def _gv(code: str, source_ref: dict, *, sensitive: bool = False) -> GlobalVariable:
    return GlobalVariable(code=code, source="department_integration", source_ref=source_ref, is_sensitive=sensitive)


def _stand(department_id: str) -> TestStand:
    return TestStand(
        id="stand_" + uuid.uuid4().hex, server_id="srv_x", department_id=department_id,
        legacy_token="stand3", queue_enabled=True, is_active=True,
    )


@pytest.fixture
def fake_reveal(monkeypatch):
    calls: list[str] = []

    async def reveal(cred_id: str) -> tuple[str, str]:
        calls.append(cred_id)
        return f"login-of-{cred_id}", f"secret-of-{cred_id}"

    monkeypatch.setattr(secret_client, "reveal_credential", reveal)
    return calls


async def _settings(**fields) -> str:
    department_id = f"dep_{uuid.uuid4().hex[:8]}"
    async with AsyncSessionLocal() as db:
        await dis_repo.create(db, {"id": f"dis_{uuid.uuid4().hex[:8]}", "department_id": department_id, **fields})
        await db.commit()
    return department_id


class TestIntegrationVariables:
    async def test_seeded_variables(self, client, no_role_token):
        for code, (source_ref, sensitive) in SEEDED.items():
            resp = await client.get(f"{VARS_BASE}/by-code/{code}", headers=_hdr(no_role_token))
            assert resp.status_code == 200, code
            body = resp.json()
            assert (body["source"], body["source_ref"], body["is_sensitive"]) == (
                "department_integration", source_ref, sensitive,
            ), code
            # Сид проходит ту же проверку, что и сохранение через API.
            assert vr.validate_source_ref("department_integration", source_ref, is_sensitive=sensitive) == source_ref

    async def test_confluence_falls_back_to_common_credential(self, fake_reveal):
        department_id = await _settings(credential_id="cred_common", stp_matrix_confluence_space="DEVQA")
        catalog = {code: _gv(code, ref, sensitive=s) for code, (ref, s) in SEEDED.items()}
        async with AsyncSessionLocal() as db:
            ctx = vr.ResolveContext(db=db, department_id=department_id, test=None, stand=None, launch_context={})
            ctx._catalog = catalog
            values = {code: await vr.resolve_code(ctx, code) for code in catalog}
        assert values["CONFLUENCE_USER"] == vr.Resolved("login-of-cred_common")
        assert values["CONFLUENCE_TOKEN"] == vr.Resolved("secret-of-cred_common", sensitive=True)
        assert values["JIRA_BASIC_AUTH"] == vr.Resolved("secret-of-cred_common", sensitive=True)
        assert values["CONFLUENCE_SPACE"] == vr.Resolved("DEVQA")
        assert fake_reveal == ["cred_common"]

    async def test_own_confluence_credential_wins(self, fake_reveal):
        department_id = await _settings(credential_id="cred_jira", confluence_credential_id="cred_conf")
        ref, sensitive = SEEDED["CONFLUENCE_TOKEN"]
        async with AsyncSessionLocal() as db:
            ctx = vr.ResolveContext(db=db, department_id=department_id, test=None, stand=None, launch_context={})
            ctx._catalog = {"CONFLUENCE_TOKEN": _gv("CONFLUENCE_TOKEN", ref, sensitive=sensitive)}
            value = await vr.resolve_code(ctx, "CONFLUENCE_TOKEN")
        assert value.value == "secret-of-cred_conf"

    async def test_nothing_configured_names_both_fields(self, fake_reveal):
        department_id = await _settings(jira_base_url="https://jira.example")
        ref, sensitive = SEEDED["CONFLUENCE_USER"]
        async with AsyncSessionLocal() as db:
            ctx = vr.ResolveContext(db=db, department_id=department_id, test=None, stand=None, launch_context={})
            ctx._catalog = {"CONFLUENCE_USER": _gv("CONFLUENCE_USER", ref, sensitive=sensitive)}
            with pytest.raises(DomainValidationError) as exc:
                await vr.resolve_code(ctx, "CONFLUENCE_USER")
        assert exc.value.error_code == "DEPARTMENT_INTEGRATION_NOT_CONFIGURED"
        assert exc.value.details["fields"] == ["confluence_credential_id", "credential_id"]
        assert fake_reveal == []

    @pytest.mark.parametrize("source_ref", [
        {"field": "confluence_credential_id", "fallback": "jira_base_url", "credential_part": "login"},
        {"field": "jira_base_url", "fallback": "credential_id"},
        {"field": "jira_base_url", "fallback": "nope"},
    ])
    async def test_invalid_fallback_is_422(self, client, admin_token, source_ref):
        resp = await client.post(VARS_BASE, headers=_hdr(admin_token), json={
            "code": f"V_{uuid.uuid4().hex[:8].upper()}", "label": "x",
            "source": "department_integration", "source_ref": source_ref,
        })
        assert resp.status_code == 422, resp.text
        assert resp.json()["error_code"] == "VARIABLE_SOURCE_REF_INVALID"


# ── каталог allta_app ────────────────────────────────────────────────────────

class TestCatalogFile:
    def _tests(self) -> list[dict]:
        return yaml.safe_load(ALLTA_CATALOG.read_text(encoding="utf-8"))["tests"]

    def test_short_names_are_legacy_tests_dict_values(self):
        tests = self._tests()
        assert len(tests) == 62
        assert {t["code"]: t["short_name"] for t in tests} == {t["code"]: legacy.tests[t["full_name"]] for t in tests}

    def test_short_names_match_data_migration(self):
        spec = importlib.util.spec_from_file_location("catalog_parity_data", DATA_MIGRATION)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        assert module._SHORT_NAMES == {t["code"]: t["short_name"] for t in self._tests()}

    def test_no_none_placeholders_left(self):
        for t in self._tests():
            for slot in t["command"]:
                assert slot.get("literal_value") != "none", t["code"]
                assert slot.get("override_value") is None, t["code"]

    def test_smolensk_has_postgresql_base_flags(self):
        """T6: `allta_back.py:29` — `postgresql-sm` → `-ps psql`, ветка `backup_image.py:333`."""
        tests = {t["code"]: t for t in self._tests()}
        base, smol = tests["postgresql.base"], tests["postgresql.smolensk"]
        assert smol["command"] == base["command"]
        assert (smol["readiness"], smol["mode"]) == ("ready", "smolensk")


class TestImportedCatalog:
    async def test_every_dates_round_trips_through_shlex(self, imported_allta_catalog):
        """`dates_quoting=shell`: `shlex.split(dates.conf)` — ровно токены слотов."""
        data = yaml.safe_load(ALLTA_CATALOG.read_text(encoding="utf-8"))
        async with AsyncSessionLocal() as db:
            for item in data["tests"]:
                test = await test_definition_repo.get_by_code(db, item["code"])
                assert test.dates_quoting == "shell", item["code"]
                assert test.short_name == item["short_name"], item["code"]
                tokens = await svc.resolve_command(db, test.id, CATALOG_CTX, stand=imported_allta_catalog)
                content = await svc.resolve_dates_content(db, test.id, CATALOG_CTX, stand=imported_allta_catalog)
                assert shlex.split(content) == tokens, item["code"]
                assert "none" not in tokens, item["code"]

    async def test_reimport_is_idempotent(self, imported_allta_catalog, capsys):
        async def snapshot() -> dict:
            data = yaml.safe_load(ALLTA_CATALOG.read_text(encoding="utf-8"))
            result = {}
            async with AsyncSessionLocal() as db:
                for item in data["tests"]:
                    test = await test_definition_repo.get_by_code(db, item["code"])
                    slots = await arg_repo.list_by_test(db, test.id)
                    result[item["code"]] = (
                        test.id, test.short_name, test.dates_quoting, test.readiness,
                        [(s.position, s.kind, s.literal_value, s.variable_id, s.override_value) for s in slots],
                    )
            return result

        before = await snapshot()
        capsys.readouterr()
        exit_code = await import_run(
            ALLTA_CATALOG, bearer_token="dbos_pat_fake_admin_token", dry_run=False,
            override_stand_server_id=imported_allta_catalog.server_id, stand_legacy_token="stand3",
        )
        assert exit_code == 0
        assert "Done: created=0 skipped=63 failed=0" in capsys.readouterr().out
        assert await snapshot() == before


# ── миграция данных ──────────────────────────────────────────────────────────

def _migrate(*args: str) -> None:
    subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=SERVICE_DIR,
        env={**os.environ, "DATABASE_URL": TEST_DATABASE_URL, "PYTHONPATH": "."},
        check=True, capture_output=True,
    )


def _slots(conn, test_id: str) -> list[tuple]:
    rows = conn.execute(
        text(
            "SELECT a.kind, a.literal_value, v.code, a.override_value FROM test_command_args a "
            "LEFT JOIN global_variables v ON v.id = a.variable_id WHERE a.test_id = :id ORDER BY a.position, a.id"
        ),
        {"id": test_id},
    ).all()
    return [lit if kind == "literal" else (f"${code}" if ov is None else (f"${code}", ov)) for kind, lit, code, ov in rows]


def test_data_migration_converts_old_import_and_is_idempotent():
    engine = create_engine(TEST_DATABASE_URL)
    suffix = uuid.uuid4().hex[:8]
    ids = {"smol": f"tdef_mig_smol_{suffix}", "xfs": f"tdef_mig_xfs_{suffix}", "own": f"tdef_mig_own_{suffix}"}
    _migrate("downgrade", f"{SCHEMA_REVISION}-1")
    try:
        with engine.begin() as conn:
            for key, code, readiness in (
                ("smol", "postgresql.smolensk", "development"),
                ("xfs", "file_systems.xfs", "ready"),
                ("own", f"custom.{suffix}", "ready"),
            ):
                conn.execute(
                    text(
                        "INSERT INTO test_definitions (id, code, full_name, readiness, created_by) "
                        "VALUES (:id, :code, :code, :readiness, 'usr_migration_test_catalog_parity')"
                    ),
                    {"id": ids[key], "code": code, "readiness": readiness},
                )
            # Старый импорт: плейсхолдеры `none`, `-fti` с override, `-fs postgresql-sm`.
            old = {
                "smol": [
                    "--username", "none", "--token", "my-own-token", "-fti", ("FOLDER_TREE_ID", "none"),
                    "-ba", "none", "-fs", "postgresql-sm", "-sn", ("STAND", None), "-tcv", ("RC", None),
                ],
                "xfs": ["--confluence-space", "none", "-fs", "xfs"],
                # Пользователь сам вписал id папки и значение — остаётся как есть.
                "own": ["-fti", ("FOLDER_TREE_ID", "777"), "--username", "someone", "none"],
            }
            for key, slots in old.items():
                for position, slot in enumerate(slots):
                    if isinstance(slot, tuple):
                        code, override = slot
                        conn.execute(
                            text(
                                "INSERT INTO test_command_args (id, test_id, position, kind, variable_id, override_value) "
                                "VALUES (:id, :test_id, :position, 'variable', "
                                "(SELECT id FROM global_variables WHERE code = :code), :override)"
                            ),
                            {"id": f"targ_{uuid.uuid4().hex}", "test_id": ids[key], "position": position,
                             "code": code, "override": override},
                        )
                    else:
                        conn.execute(
                            text(
                                "INSERT INTO test_command_args (id, test_id, position, kind, literal_value) "
                                "VALUES (:id, :test_id, :position, 'literal', :value)"
                            ),
                            {"id": f"targ_{uuid.uuid4().hex}", "test_id": ids[key], "position": position,
                             "value": slot},
                        )
        _migrate("upgrade", "head")

        def state() -> dict:
            with engine.connect() as conn:
                tests = {
                    row.id: (row.short_name, row.readiness, row.dates_quoting)
                    for row in conn.execute(
                        text("SELECT id, short_name, readiness, dates_quoting FROM test_definitions WHERE id IN :ids")
                        .bindparams(bindparam("ids", expanding=True)),
                        {"ids": list(ids.values())},
                    )
                }
                return {key: (tests[test_id], _slots(conn, test_id)) for key, test_id in ids.items()}

        after = state()
        assert after["smol"] == (("postgresql-sm", "ready", "shell"), [
            "--username", "$CONFLUENCE_USER", "--token", "my-own-token", "-fti", "$FOLDER_TREE_ID",
            "-ba", "$JIRA_BASIC_AUTH", "-db", "-sn", "$STAND", "-tcv", "$RC_NAME",
            "-c", "--package", "postgresql-11",
        ])
        assert after["xfs"] == (("XFS", "ready", "shell"), ["--confluence-space", "$CONFLUENCE_SPACE", "-fs", "xfs"])
        assert after["own"] == ((None, "ready", "shell"), [
            "-fti", ("$FOLDER_TREE_ID", "777"), "--username", "someone", "none",
        ])

        # Повторный прогон шага данных ничего не меняет.
        spec = importlib.util.spec_from_file_location("catalog_parity_data_reimport", DATA_MIGRATION)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with engine.begin() as conn:
            with Operations.context(MigrationContext.configure(conn)):
                module.upgrade()
        assert state() == after
    finally:
        _migrate("upgrade", "head")
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM test_definitions WHERE created_by = 'usr_migration_test_catalog_parity'"))
        engine.dispose()
