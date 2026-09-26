"""FreeIPA как многостендовый сценарий.

* превью сценария (КД-исполнитель + ВМ-клиент) даёт для исполнителя скрипт
  с легаси-строкой `cd …/freeipa && <venv> ipa_run.py {dates}` и
  `tokens.json` (`srv_pass`, секрет под маской); dates — те же, что у легаси
  `freeipa_authentication_test` (`backup_image.py:943-964`), секреты — `***`;
* адреса КД и клиента можно доставить файлом профиля через переменные
  `stand_ref` (ветка `freeipa` читает `ipa_conf.HOSTS` из кода — см. README).
"""

from __future__ import annotations

import shlex
import uuid

from src.db.session import AsyncSessionLocal
from src.models import LaunchProfileVersion, TestStand
from src.repositories import test_definition as test_definition_repo
from src.services import launch_profile as lp_svc
from src.services import server_client
from src.services.launch_profile import RenderedPaths
from src.services.variable_resolver import ResolveContext
from tests.conftest import auth_hdr as _hdr
from tests.legacy_parity import LegacyCredentials, argv_pairs, legacy_parent_page, legacy_run, short_name_of
from tests.legacy_parity import legacy
from tests.test_launch_context import (  # noqa: F401 — фикстуры каталога
    CATALOG_FOLDER_TREE_ID,
    OS_VERSIONS,
    imported_allta_catalog,
    integration_for_catalog,
    mock_server_service,
    os_version_catalog,
    zephyr_folder_for_catalog,
)

SCENARIOS = "/api/testing/v1/scenarios"
VARS = "/api/testing/v1/global-variables"
CTX = {"RC": "osv_5e46b2d1", "KERNEL": "6.1.0", "MODE": "orel"}
HOSTS = {"dc": "10.177.103.204", "client": "10.177.103.201"}  # ipa_conf.HOSTS легаси


async def _client_vm_stand() -> str:
    stand_id = f"stand_{uuid.uuid4().hex}"
    async with AsyncSessionLocal() as db:
        db.add(TestStand(
            id=stand_id, target_type="vm", vm_id=f"vm_{uuid.uuid4().hex[:10]}", server_id=None,
            department_id="dep_a", created_by="usr_test_fixture",
        ))
        await db.commit()
    return stand_id


def _stand_hosts(monkeypatch, dc: TestStand, client_id: str) -> None:
    async def connection(target):
        return {"host": HOSTS["dc"] if target.id == dc.server_id else HOSTS["client"]}

    monkeypatch.setattr(server_client, "get_stand_connection_info", connection)


class TestFreeipaScenarioPreview:
    async def test_executor_gets_legacy_ipa_run_line_and_tokens_json(
        self, client, admin_token, imported_allta_catalog, integration_for_catalog, os_version_catalog, monkeypatch,
    ):
        dc = imported_allta_catalog
        client_stand = await _client_vm_stand()
        _stand_hosts(monkeypatch, dc, client_stand)
        async with AsyncSessionLocal() as db:
            test = await test_definition_repo.get_by_code(db, "freeipa.auth")
        assert test.launch_profile_id == "lp_freeipa"

        resp = await client.post(SCENARIOS, headers=_hdr(admin_token), json={
            "code": f"freeipa.{uuid.uuid4().hex[:6]}", "name": "FreeIPA auth", "department_id": "dep_a",
            "stands": [
                {"stand_id": dc.id, "label": "КД и исполнитель", "preparation": "full"},
                {"stand_id": client_stand, "label": "клиент", "preparation": "revert_only",
                 "kernel_override": "5.15.0-83-generic"},
            ],
            "actions": [{"kind": "run_test", "stand_id": dc.id, "test_id": test.id, "is_verdict": True}],
        })
        assert resp.status_code == 201, resp.text
        preview = await client.post(
            f"{SCENARIOS}/{resp.json()['id']}/preview", headers=_hdr(admin_token),
            json={"os_version_id": CTX["RC"], "kernel": CTX["KERNEL"]},
        )
        assert preview.status_code == 200, preview.text
        launch = preview.json()["actions"][0]["launch"]
        assert launch["launch_profile"]["profile_id"] == "lp_freeipa"
        files = {f["role"]: f for f in launch["files"]}

        # tokens.json для ipa_conf.py: пароль тестовой учётки, под маской.
        tokens = next(f for f in launch["files"] if f["path"].endswith("/tokens.json"))
        assert tokens["role"] == "extra" and tokens["sensitive"] and tokens["mode"] == "0600"
        assert tokens["content"] == '{"srv_pass": "***"}\n'

        # Команда: легаси-строка ipa_run.py с dates строкой.
        line = next(
            ln for ln in files["script"]["content"].splitlines() if ln.startswith("cd ") and " ipa_run.py " in ln
        )
        ours_cd, ours_dates = line.split(" ipa_run.py ", 1)
        assert ours_cd == f"cd /home/u/freeipa_test/gitipa/stress_test/freeipa && {legacy.VENV_PATH}"
        assert shlex.split(ours_dates) == shlex.split(launch["dates_content_masked"])
        assert shlex.split(launch["launch_command_masked"])[-1] == "freeipa"

        conf_login, conf_token = integration_for_catalog["cred_conf"]
        _jira_login, jira_token = integration_for_catalog["cred_jira"]
        release = OS_VERSIONS[CTX["RC"]].name
        short = short_name_of(test.full_name)
        run = legacy_run(
            test=short, release=release, mode=CTX["MODE"], kernel=CTX["KERNEL"], stand="stand3",
            tcase=test.full_name, cti=CATALOG_FOLDER_TREE_ID, parent_page=legacy_parent_page(release, short),
            creds=LegacyCredentials(username=conf_login, conf_token=conf_token, jira_token=jira_token, git_token="t"),
        )
        legacy_line = [e.command for e in run.events if e.where == "host"][-1]
        legacy_cd, legacy_dates = legacy_line.split(" ipa_run.py ", 1)
        assert ours_cd == legacy_cd
        # Секреты легаси — `***`, как в превью.
        theirs = ["***" if tok in (conf_token, jira_token) else tok for tok in shlex.split(legacy_dates)]
        assert argv_pairs(shlex.split(ours_dates)) == argv_pairs(theirs)


class TestHostsFileViaStandRef:
    async def test_extra_file_carries_scenario_stand_addresses(self, client, admin_token, monkeypatch):
        """Ветка freeipa берёт HOSTS из кода; профиль умеет доставить адреса
        стендов сценария файлом (`stand_ref`), если ветке дадут их читать."""
        async with AsyncSessionLocal() as db:
            dc = TestStand(id=f"stand_{uuid.uuid4().hex}", server_id=f"srv_{uuid.uuid4().hex[:10]}",
                           department_id="dep_a", created_by="usr_test_fixture")
            db.add(dc)
            await db.commit()
        client_stand = await _client_vm_stand()
        _stand_hosts(monkeypatch, dc, client_stand)
        codes = {}
        for role, stand_id in (("DC", dc.id), ("CLIENT", client_stand)):
            code = f"IPA_{role}_HOST_{uuid.uuid4().hex[:4].upper()}"
            resp = await client.post(VARS, headers=_hdr(admin_token), json={
                "code": code, "label": code, "source": "stand_ref",
                "source_ref": {"stand_id": stand_id, "field": "host"},
            })
            assert resp.status_code == 201, resp.text
            codes[role] = code

        version = LaunchProfileVersion(
            id="lpv_x", profile_id="lp_x", version=1, starter_script="#!/bin/bash\n", clone={},
            paths={}, launch_command_template="true", stop_command_template="true",
            stop_grace_seconds=1, use_pty=False, testenv={},
            extra_files=[{
                "path": "/home/u/freeipa_test/hosts.json",
                "content": f'{{"dc": "{{{{{codes["DC"]}}}}}", "client": "{{{{{codes["CLIENT"]}}}}}"}}',
                "mode": "0644",
            }],
        )
        paths = RenderedPaths(
            script="/home/u/s.sh", dates="/home/u/d.conf", token="/home/u/t.conf",
            testenv_marker="/home/u/m.conf", command_file="/home/u/c.txt",
        )
        async with AsyncSessionLocal() as db:
            ctx = ResolveContext(db=db, department_id="dep_a", test=None, stand=None, launch_context={})
            launch = await lp_svc.build_launch(
                ctx, version, paths, dates_content="", dates_content_masked="", git_token="",
                testenv_on=False, prepare_only=False,
            )
        hosts = next(f for f in launch.files if f["path"] == "/home/u/freeipa_test/hosts.json")
        assert hosts["content"] == '{"dc": "10.177.103.204", "client": "10.177.103.201"}'


def _load_client_skip_pam_migration():
    import importlib.util
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[1]
        / "src" / "db" / "migrations" / "versions" / "e7c2a9d4f158_freeipa_client_skip_pam_fix.py"
    )
    spec = importlib.util.spec_from_file_location("freeipa_client_skip_pam_fix", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestClientSkipPamFixMigration:
    async def test_only_seed_client_stand_gets_skip_pam_fix(self, client, monkeypatch):
        """Легаси готовил клиента FreeIPA без PAM-правки: флаг получает только
        `revert_only`-стенд несозданного владельцем сценария; повтор — no-op."""
        from types import SimpleNamespace

        from sqlalchemy import create_engine, text

        from tests.conftest import TEST_DATABASE_URL

        migration = _load_client_skip_pam_migration()
        engine = create_engine(TEST_DATABASE_URL, isolation_level="AUTOCOMMIT")
        seed, owned = f"scn_{uuid.uuid4().hex}", f"scn_{uuid.uuid4().hex}"
        stands = {key: f"ss_{uuid.uuid4().hex}" for key in ("seed_dc", "seed_client", "owned_client")}

        def flags() -> dict[str, bool]:
            with engine.connect() as conn:
                rows = conn.execute(text("SELECT id, skip_pam_fix FROM scenario_stands WHERE id = ANY(:ids)"),
                                    {"ids": list(stands.values())}).all()
            by_id = dict(rows)
            return {key: by_id[sid] for key, sid in stands.items()}

        def run(step: str) -> None:
            with engine.connect() as conn:
                monkeypatch.setattr(migration, "op", SimpleNamespace(get_bind=lambda: conn))
                getattr(migration, step)()

        try:
            with engine.connect() as conn:
                for scn_id, code, created_by in ((seed, "freeipa.auth", None), (owned, "freeipa.plugin", "usr_owner")):
                    conn.execute(text(
                        "INSERT INTO scenarios (id, code, name, department_id, created_by) "
                        "VALUES (:id, :code, :code, 'dep_a', :by)"
                    ), {"id": scn_id, "code": code, "by": created_by})
                for key, scn_id, pos, prep in (("seed_dc", seed, 0, "full"), ("seed_client", seed, 1, "revert_only"),
                                               ("owned_client", owned, 1, "revert_only")):
                    conn.execute(text(
                        "INSERT INTO scenario_stands (id, scenario_id, stand_id, position, preparation) "
                        "VALUES (:id, :scn, :stand, :pos, :prep)"
                    ), {"id": stands[key], "scn": scn_id, "stand": f"stand_{uuid.uuid4().hex}", "pos": pos,
                        "prep": prep})

            run("upgrade")
            assert flags() == {"seed_dc": False, "seed_client": True, "owned_client": False}
            run("upgrade")
            assert flags() == {"seed_dc": False, "seed_client": True, "owned_client": False}
            run("downgrade")
            assert flags() == {"seed_dc": False, "seed_client": False, "owned_client": False}
        finally:
            with engine.connect() as conn:
                conn.execute(text("DELETE FROM scenarios WHERE id = ANY(:ids)"), {"ids": [seed, owned]})
            engine.dispose()
