"""psql/tantor kernels — 4 шага по легаси `db_kernel_changer`.

* golden: для каждого шага импортированного каталога `shlex.split(dates)` даёт
  те же пары «флаг → значение», что легаси `db_kernel_changer`
  (`backup_image.py:895-941`, вызов 1028-1032) с настоящими учётными
  данными; порт построения строки — ниже, без импорта легаси;
* шаги: `maxcpus` через `kernel_cmdline_extra`, первый `full`, остальные
  `rerun`, у tantor — рестарт сервиса перед повторными фазами;
* миграция `tp14_test_steps`: у каждого теста — шаг с его `stand_setup`/
  `starter_suffix` и слотами; kernels из одного шага → 4; изменённые руками
  слоты не трогаются; downgrade возвращает первый шаг в тест.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from src.db.session import AsyncSessionLocal
from src.repositories import launch_profile as launch_profile_repo
from src.repositories import test_definition as test_definition_repo
from src.repositories import test_step as test_step_repo
from src.services import test_command_arg as svc
from tests.conftest import TEST_DATABASE_URL
from tests.legacy_parity import legacy
from tests.test_launch_context import (  # noqa: F401 — фикстуры каталога
    CATALOG_CTX,
    CATALOG_FOLDER_TREE_ID,
    OS_VERSIONS,
    imported_allta_catalog,
    integration_for_catalog,
    mock_server_service,
    os_version_catalog,
    zephyr_folder_for_catalog,
)
from tests.test_queue import recorded_calls  # noqa: F401 — зависимость mock_server_service

SERVICE_DIR = Path(__file__).resolve().parents[1]
PREVIOUS_REVISION = "e4c7b2a91d35"
KERNEL_PHASES = ((8, "begin"), (16, None), (24, None), (32, "end"))


def _legacy_db_kernel_dates(
    *, database: str, cpu_count: int, position: str | None, username: str, conf_token: str,
    jira_token: str, test: str, release: str, mode: str, kernel: str, stand: str, cti: str,
    tcase: str, topic: str,
) -> str:
    """Порт `backup_image.py:293-313,895-918` (`db_kernel_changer`), дословно.

    `parent_page` — `allta_image_conf.py:122-127`, `TCYCLE` — `allta_back.py:175`,
    `ST` — `allta_back.py:169`.
    """
    parent_page = f"STRESS_report {release} ⬝ {topic}"
    st = stand.replace("stand", "")
    tcycle = f"{release}_{mode}_{kernel}_{stand}"
    username = f'--username {username}'
    token = f'--token {conf_token}'
    confluence_space = "--confluence-space 'DEVQA'"
    confluence_parent_page = f'--confluence-parent-page "{parent_page}"'
    confluence_new_page = f'--confluence-new-page "{test}_{release}_{mode}_{kernel}_{stand}"'
    sn = f'-sn {st}'
    fti = f'-fti {cti}'
    tcyc = f'-tcyc {tcycle}'
    tcas = f'-tcas "{tcase}"'
    ba = f'-ba "{jira_token}"'
    tcv = f'-tcv {release}'
    psql_version = '--package postgresql-'
    tantor_pkg = '--package tantor-se-server-15'

    test_args = f'{username} {token} {confluence_space} {confluence_parent_page} {confluence_new_page} \
                   {sn} {fti} {tcyc} {tcas} {ba} {tcv} -q {cpu_count}'
    begin_args = test_args + ' -sf begin'
    end_args = test_args + ' -sf end'

    if position == 'begin':
        if database == 'tantor':
            dates = begin_args + f' {tantor_pkg} -db tantor'
        elif database == 'psql':
            dates = begin_args + f' {psql_version}'
    elif position == 'end':
        if database == 'tantor':
            dates = end_args + f' {tantor_pkg} -db tantor'
        elif database == 'psql':
            dates = end_args + f' {psql_version}'
    else:
        if database == 'tantor':
            dates = test_args + f' {tantor_pkg} -db tantor'
        elif database == 'psql':
            dates = test_args + f' {psql_version}'
    return dates


KERNEL_TESTS = {"postgresql.kernels": "psql", "postgresql.tantor_kernels": "tantor"}


class TestKernelsCatalog:
    @pytest.mark.parametrize(("code", "database"), list(KERNEL_TESTS.items()))
    async def test_four_dates_match_legacy_db_kernel_changer(
        self, imported_allta_catalog, integration_for_catalog, code, database,
    ):
        """Критерий приёмки: 4 dates-строки = легаси `db_kernel_changer`."""
        conf_login, conf_token = integration_for_catalog["cred_conf"]
        _jira_login, jira_token = integration_for_catalog["cred_jira"]
        async with AsyncSessionLocal() as db:
            test = await test_definition_repo.get_by_code(db, code)
            steps = await test_step_repo.list_by_test(db, test.id)
            assert len(steps) == 4
            for step, (cpu_count, position) in zip(steps, KERNEL_PHASES):
                content = await svc.resolve_dates_content(
                    db, test.id, CATALOG_CTX, stand=imported_allta_catalog, step_id=step.id,
                )
                expected = _legacy_db_kernel_dates(
                    database=database, cpu_count=cpu_count, position=position,
                    username=conf_login, conf_token=conf_token, jira_token=jira_token,
                    test=legacy.tests[test.full_name], release=OS_VERSIONS[CATALOG_CTX["RC"]].name,
                    mode=CATALOG_CTX["MODE"], kernel=CATALOG_CTX["KERNEL"], stand="stand3",
                    cti=CATALOG_FOLDER_TREE_ID, tcase=test.full_name, topic="PostgreSQL",
                )
                ours, theirs = shlex.split(content), shlex.split(expected)
                assert legacy.argv_pairs(ours) == legacy.argv_pairs(theirs), (code, cpu_count)
                assert sorted(ours) == sorted(theirs), (code, cpu_count)

    @pytest.mark.parametrize(("code", "database"), list(KERNEL_TESTS.items()))
    async def test_steps_follow_legacy_phases(self, imported_allta_catalog, code, database):
        async with AsyncSessionLocal() as db:
            test = await test_definition_repo.get_by_code(db, code)
            steps = await test_step_repo.list_by_test(db, test.id)
        assert test.readiness == "ready"
        assert [s.run_mode for s in steps] == ["full", "rerun", "rerun", "rerun"]
        assert [s.starter_suffix for s in steps] == ["kernel"] * 4
        assert [s.stand_setup["kernel_cmdline_extra"] for s in steps] == [
            [f"maxcpus={cpu}"] for cpu, _ in KERNEL_PHASES
        ]
        scripts = [(s.stand_setup.get("script") or "") for s in steps]
        if database == "tantor":
            assert scripts == ["", *["systemctl restart tantor-se-server-15.service\n"] * 3]
            assert [s.stand_setup.get("reboot_after") for s in steps[1:]] == [False] * 3
        else:
            assert scripts == [""] * 4

    async def test_default_launch_profile_has_legacy_rerun_script(self):
        async with AsyncSessionLocal() as db:
            version = await launch_profile_repo.get_version(db, "lpv_default_1")
        # backup_image.py:937-939 + VENV_PATH (allta_image_conf.py:16).
        assert 'cd /home/u/git/stress_test/"$1"/' in version.rerun_script
        assert '/home/u/python/Python-3.12.1/venv/bin/python3.12 run.py -n "$3" -kn "$5"' in version.rerun_script
        assert "clone" not in version.rerun_script


# ── миграция ─────────────────────────────────────────────────────────────────

def _migrate(*args: str) -> None:
    subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=SERVICE_DIR,
        env={**os.environ, "DATABASE_URL": TEST_DATABASE_URL, "PYTHONPATH": "."},
        check=True, capture_output=True,
    )


def _insert_test(conn, test_id: str, code: str, *, stand_setup: dict | None, suffix: str | None, slots: list) -> None:
    conn.execute(
        text(
            "INSERT INTO test_definitions (id, code, full_name, readiness, created_by, stand_setup, starter_suffix) "
            "VALUES (:id, :code, :code, 'development', 'usr_migration_test_tp14', CAST(:setup AS jsonb), :suffix)"
        ),
        {"id": test_id, "code": code, "setup": None if stand_setup is None else json.dumps(stand_setup), "suffix": suffix},
    )
    for position, slot in enumerate(slots):
        if isinstance(slot, tuple):
            conn.execute(
                text(
                    "INSERT INTO test_command_args (id, test_id, position, kind, variable_id) "
                    "VALUES (:id, :test_id, :position, 'variable', (SELECT id FROM global_variables WHERE code = :code))"
                ),
                {"id": f"targ_{uuid.uuid4().hex}", "test_id": test_id, "position": position, "code": slot[0]},
            )
        else:
            conn.execute(
                text(
                    "INSERT INTO test_command_args (id, test_id, position, kind, literal_value) "
                    "VALUES (:id, :test_id, :position, 'literal', :value)"
                ),
                {"id": f"targ_{uuid.uuid4().hex}", "test_id": test_id, "position": position, "value": slot},
            )


def _step_rows(conn, test_id: str) -> list:
    return conn.execute(
        text("SELECT id, position, name, run_mode, starter_suffix, stand_setup FROM test_steps "
             "WHERE test_id = :t ORDER BY position"),
        {"t": test_id},
    ).all()


def _step_slots(conn, step_id: str) -> list:
    rows = conn.execute(
        text(
            "SELECT a.kind, a.literal_value, v.code FROM test_command_args a "
            "LEFT JOIN global_variables v ON v.id = a.variable_id WHERE a.step_id = :s ORDER BY a.position"
        ),
        {"s": step_id},
    ).all()
    return [lit if kind == "literal" else f"${code}" for kind, lit, code in rows]


def test_migration_moves_tests_to_steps_and_splits_kernels():
    engine = create_engine(TEST_DATABASE_URL)
    suffix = uuid.uuid4().hex[:8]
    ids = {k: f"tdef_mig14_{k}_{suffix}" for k in ("own", "psql", "tantor")}
    own_setup = {"kernel_cmdline_extra": ["audit=0"], "script": "", "run_as": "root",
                 "phase": "after_boot", "reboot_after": None, "timeout_seconds": 1800}
    kernels_slots = ["-sn", ("STAND",), "-q", "8", "-sf", "begin", "--package", "postgresql-"]
    _migrate("downgrade", PREVIOUS_REVISION)
    try:
        with engine.begin() as conn:
            _insert_test(conn, ids["own"], f"custom.{suffix}", stand_setup=own_setup, suffix="oom", slots=["-x", "1"])
            _insert_test(conn, ids["psql"], "postgresql.kernels", stand_setup=None, suffix="kernel", slots=kernels_slots)
            # Изменённые руками слоты (`-q 4`) — тест не трогается.
            _insert_test(conn, ids["tantor"], "postgresql.tantor_kernels", stand_setup=None, suffix="kernel",
                         slots=["-q", "4", "--package", "tantor-se-server-15"])
        _migrate("upgrade", "head")

        with engine.connect() as conn:
            [own] = _step_rows(conn, ids["own"])
            assert (own.position, own.run_mode, own.starter_suffix, own.stand_setup) == (0, "full", "oom", own_setup)
            assert _step_slots(conn, own.id) == ["-x", "1"]

            psql = _step_rows(conn, ids["psql"])
            assert [s.name for s in psql] == ["maxcpus=8 · begin", "maxcpus=16", "maxcpus=24", "maxcpus=32 · end"]
            assert [s.run_mode for s in psql] == ["full", "rerun", "rerun", "rerun"]
            assert [s.starter_suffix for s in psql] == ["kernel"] * 4
            assert [s.stand_setup["kernel_cmdline_extra"] for s in psql] == [
                ["maxcpus=8"], ["maxcpus=16"], ["maxcpus=24"], ["maxcpus=32"],
            ]
            assert [_step_slots(conn, s.id) for s in psql] == [
                ["-sn", "$STAND", "-q", "8", "-sf", "begin", "--package", "postgresql-"],
                ["-sn", "$STAND", "-q", "16", "--package", "postgresql-"],
                ["-sn", "$STAND", "-q", "24", "--package", "postgresql-"],
                ["-sn", "$STAND", "-q", "32", "-sf", "end", "--package", "postgresql-"],
            ]
            readiness = dict(conn.execute(
                text("SELECT id, readiness FROM test_definitions WHERE created_by = 'usr_migration_test_tp14'"),
            ).all())
            assert readiness[ids["psql"]] == "ready"
            assert readiness[ids["tantor"]] == "development"
            [tantor] = _step_rows(conn, ids["tantor"])
            assert _step_slots(conn, tantor.id) == ["-q", "4", "--package", "tantor-se-server-15"]
            assert tantor.stand_setup is None

        # Обратно: значения первого шага — в тест, остальные шаги теряются.
        _migrate("downgrade", PREVIOUS_REVISION)
        with engine.connect() as conn:
            rows = {
                r.id: (r.stand_setup, r.starter_suffix)
                for r in conn.execute(text(
                    "SELECT id, stand_setup, starter_suffix FROM test_definitions "
                    "WHERE created_by = 'usr_migration_test_tp14'"
                ))
            }
            assert rows[ids["own"]] == (own_setup, "oom")
            assert rows[ids["psql"]][0]["kernel_cmdline_extra"] == ["maxcpus=8"]
            count = conn.execute(
                text("SELECT count(*) FROM test_command_args WHERE test_id = :t"), {"t": ids["psql"]},
            ).scalar()
            assert count == len(kernels_slots)
    finally:
        _migrate("upgrade", "head")
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM test_definitions WHERE created_by = 'usr_migration_test_tp14'"))
        engine.dispose()
