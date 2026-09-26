"""FreeIPA as a multi-stand scenario (launch profile extra files, freeipa-runner profile, seed scenarios)

Revision ID: a6d1f3b7c924
Revises: f2a9c6e1b358
Create Date: 2026-09-25 00:30:00.000000

Решение D12.2. Легаси `emm/allta_app_full/backup_image.py:943-964`
(`freeipa_authentication_test`): откат ВМ-клиента `work-station1` (stand1)
на снимок `1.7.5.9`, подготовка клиента с ядром `5.15.0-83-generic` без смены
режима, затем на хосте ALLTA:

    cd /home/u/freeipa_test/gitipa && <venv> git_clone.py
    cd /home/u/freeipa_test/gitipa/stress_test && git checkout freeipa
    cd /home/u/freeipa_test/gitipa/stress_test/freeipa && <venv> ipa_run.py {dates}

* `launch_profile_versions.extra_files` — дополнительные файлы профиля
  : ветке `freeipa` нужен `/home/u/tokens.json`
  (`{"srv_pass": …}`, `ipa_conf.py`).
* Профиль «FreeIPA (ipa_run.py)» `lp_freeipa`: общий, не по умолчанию. Клон
  ветки теста (`freeipa`) в `/home/u/freeipa_test/gitipa/stress_test` —
  то же, что `git_clone.py` + `git checkout freeipa`; запуск — легаси-строкой
  `cd … && <venv> ipa_run.py {dates}`, dates — строкой (`{{DATES_INLINE}}`),
  не файлом. Репозиторий, команда остановки, pty, testenv — как у
  действующей версии общего профиля.
* Тесты `freeipa.*` без своего профиля получают `lp_freeipa`.
* Сценарии `freeipa.auth` / `freeipa.create_users` / `freeipa.plugin` —
  по одному на тест (выбор теста при запуске сценарий не поддерживает, а
  стенды и вердикт у тестов одни и те же), `readiness=development`. Стенды —
  если есть в БД: КД — стенд `stand3` (`full`), клиент — ВМ-стенд `stand1`
  (`revert_only`, ядро `5.15.0-83-generic`, режим — как у запуска). Исполнитель
  `ipa_run.py` — КД: легаси запускал его с хоста ALLTA, которого в пуле нет,
  а `ipa_run.py` ходит на хосты `ipa_conf.HOSTS` по SSH откуда угодно. Нет
  `stand3` — сценарий без стендов и действий, стенды выбирает владелец в UI.
  Отдел — отдел `stand3`, иначе отдел по умолчанию легаси-compat;
  нет и его — сценарии не создаются (их заводит владелец).
"""
from typing import Sequence, Union
from uuid import uuid4

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a6d1f3b7c924"
down_revision: Union[str, None] = "f2a9c6e1b358"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

PROFILE_ID = "lp_freeipa"
VERSION_ID = "lpv_freeipa_1"
VENV = "/home/u/python/Python-3.12.1/venv/bin/python3.12"  # allta_image_conf.py:16
LEGACY_CLIENT_KERNEL = "5.15.0-83-generic"  # backup_image.py:947
FREEIPA_TESTS = ("freeipa.auth", "freeipa.create_users", "freeipa.plugin")

STARTER_SCRIPT = (
    "#!/bin/bash\n"
    "\n"
    "# FreeIPA: легаси backup_image.py:943-964 — клон ветки freeipa\n"
    "# (git_clone.py + git checkout freeipa) и ipa_run.py {dates} строкой.\n"
    "# $1 — ветка теста (freeipa).\n"
    'testenv=`cat "{{TESTENV_MARKER_PATH}}" 2>/dev/null`\n'
    "mkdir -p /home/u/freeipa_test/gitipa\n"
    "cd /home/u/freeipa_test/gitipa\n"
    "rm -rf /home/u/freeipa_test/gitipa/stress_test\n"
    'git -c http.extraHeader="Authorization: $(cat "{{GIT_TOKEN_PATH}}")" clone {{GIT_CLONE_ARGS}} '
    '{{GIT_REPO_URL}}; rm -f "{{GIT_TOKEN_PATH}}"\n'
    "\n"
    "if [[ \"$testenv\" == 'on' ]]; then\n"
    "    echo 'Подготовка тестового окружения завершена'\n"
    "    exit 0\n"
    "fi\n"
    f"cd /home/u/freeipa_test/gitipa/stress_test/freeipa && {VENV} ipa_run.py {{{{DATES_INLINE}}}}\n"
)
PATHS = {
    "script": "{TEST_HOME}/freeipa_starter.sh",
    "dates": "{TEST_HOME}/dates_{QUEUE_ITEM_ID}.conf",
    "token": "{TEST_HOME}/git_token_{QUEUE_ITEM_ID}.conf",
    "testenv_marker": "{TEST_HOME}/testenv_marker.conf",
    "command_file": "{TEST_HOME}/command.txt",
}
# `ipa_conf.py` ветки freeipa читает пароль стендов из /home/u/tokens.json.
EXTRA_FILES = [{
    "path": "{TEST_HOME}/tokens.json",
    "content": '{"srv_pass": "{{TEST_PASSWORD}}"}\n',
    "mode": "0600",
    "sensitive": True,
}]


def _jsonb(name: str) -> sa.BindParameter:
    return sa.bindparam(name, type_=postgresql.JSONB(none_as_null=True))


def upgrade() -> None:
    op.add_column(
        "launch_profile_versions",
        sa.Column("extra_files", postgresql.JSONB(), nullable=False, server_default="[]"),
    )
    conn = op.get_bind()

    base = conn.execute(sa.text(
        "SELECT v.clone, v.stop_command_template, v.stop_grace_seconds, v.use_pty, v.testenv "
        "FROM launch_profiles p JOIN launch_profile_versions v ON v.id = p.current_version_id "
        "WHERE p.department_id IS NULL AND p.is_default"
    )).first()
    if base is not None and conn.execute(
        sa.text("SELECT 1 FROM launch_profiles WHERE id = :id"), {"id": PROFILE_ID},
    ).first() is None:
        conn.execute(sa.text(
            "INSERT INTO launch_profiles (id, department_id, name, is_default, current_version_id) "
            "VALUES (:id, NULL, 'FreeIPA (ipa_run.py)', false, NULL)"
        ), {"id": PROFILE_ID})
        conn.execute(sa.text(
            "INSERT INTO launch_profile_versions (id, profile_id, version, comment, starter_script, clone, "
            "paths, launch_command_template, stop_command_template, stop_grace_seconds, use_pty, testenv, "
            "rerun_script, extra_files) VALUES (:id, :profile_id, 1, :comment, :script, :clone, :paths, "
            ":launch, :stop, :grace, :pty, :testenv, NULL, :extra)"
        ).bindparams(_jsonb("clone"), _jsonb("paths"), _jsonb("testenv"), _jsonb("extra")), {
            "id": VERSION_ID, "profile_id": PROFILE_ID,
            "comment": "легаси freeipa_authentication_test (backup_image.py:943-964)",
            "script": STARTER_SCRIPT, "clone": {**dict(base.clone), "mode": "branch"}, "paths": PATHS,
            "launch": "bash {STARTER_PATH} {TEST_BRANCH}",
            "stop": base.stop_command_template, "grace": base.stop_grace_seconds, "pty": base.use_pty,
            "testenv": dict(base.testenv), "extra": EXTRA_FILES,
        })
        conn.execute(sa.text("UPDATE launch_profiles SET current_version_id = :v WHERE id = :id"),
                     {"v": VERSION_ID, "id": PROFILE_ID})
        conn.execute(sa.text(
            "UPDATE test_definitions SET launch_profile_id = :p "
            "WHERE code = ANY(:codes) AND launch_profile_id IS NULL"
        ), {"p": PROFILE_ID, "codes": list(FREEIPA_TESTS)})

    _seed_scenarios(conn)


def _seed_scenarios(conn) -> None:
    dc = conn.execute(sa.text(
        "SELECT id, department_id FROM test_stands WHERE legacy_token = 'stand3'"
    )).first()
    department_id = dc.department_id if dc is not None else None
    if department_id is None:
        row = conn.execute(sa.text("SELECT default_department_id FROM legacy_compat_settings")).first()
        department_id = row.default_department_id if row is not None else None
    if department_id is None:
        return
    client = conn.execute(sa.text(
        "SELECT id FROM test_stands WHERE legacy_token = 'stand1' AND target_type = 'vm' "
        "AND department_id = :d"
    ), {"d": department_id}).first()

    for code in FREEIPA_TESTS:
        test = conn.execute(sa.text(
            "SELECT id, full_name FROM test_definitions WHERE code = :c "
            "AND (department_id IS NULL OR department_id = :d)"
        ), {"c": code, "d": department_id}).first()
        if test is None or conn.execute(
            sa.text("SELECT 1 FROM scenarios WHERE code = :c"), {"c": code},
        ).first() is not None:
            continue
        scenario_id = f"scn_{uuid4().hex}"
        conn.execute(sa.text(
            "INSERT INTO scenarios (id, code, name, department_id, readiness) "
            "VALUES (:id, :code, :name, :d, 'development')"
        ), {"id": scenario_id, "code": code, "name": f"FreeIPA: {test.full_name}", "d": department_id})
        if dc is None:
            continue
        dc_row = f"scs_{uuid4().hex}"
        conn.execute(sa.text(
            "INSERT INTO scenario_stands (id, scenario_id, stand_id, position, label, preparation) "
            "VALUES (:id, :s, :stand, 0, 'КД и исполнитель ipa_run.py', 'full')"
        ), {"id": dc_row, "s": scenario_id, "stand": dc.id})
        if client is not None:
            conn.execute(sa.text(
                "INSERT INTO scenario_stands (id, scenario_id, stand_id, position, label, preparation, "
                "kernel_override) VALUES (:id, :s, :stand, 1, 'клиент (ВМ)', 'revert_only', :k)"
            ), {"id": f"scs_{uuid4().hex}", "s": scenario_id, "stand": client.id, "k": LEGACY_CLIENT_KERNEL})
        conn.execute(sa.text(
            "INSERT INTO scenario_actions (id, scenario_id, position, kind, scenario_stand_id, test_id, "
            "is_verdict, params) VALUES (:id, :s, 0, 'run_test', :row, :t, true, '{}')"
        ), {"id": f"sca_{uuid4().hex}", "s": scenario_id, "row": dc_row, "t": test.id})


def downgrade() -> None:
    conn = op.get_bind()
    scenario_ids = [r.id for r in conn.execute(sa.text(
        "SELECT id FROM scenarios WHERE code = ANY(:codes) AND created_by IS NULL "
        "AND NOT EXISTS (SELECT 1 FROM scenario_runs r WHERE r.scenario_id = scenarios.id)"
    ), {"codes": list(FREEIPA_TESTS)})]
    if scenario_ids:
        conn.execute(sa.text("DELETE FROM scenarios WHERE id = ANY(:ids)"), {"ids": scenario_ids})
    conn.execute(sa.text("UPDATE test_definitions SET launch_profile_id = NULL WHERE launch_profile_id = :p"),
                 {"p": PROFILE_ID})
    conn.execute(sa.text("DELETE FROM launch_profiles WHERE id = :p"), {"p": PROFILE_ID})
    op.drop_column("launch_profile_versions", "extra_files")
