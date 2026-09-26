"""Launch profiles (starter.sh, paths, stop command, pty, testenv)

Revision ID: c1f7a3e9d402
Revises: b8e4f1a7c265
Create Date: 2026-09-24 00:00:00.000000

Решения D16, T1, T2, T5 плана паритета: `starter.sh`, пути на
стенде, команда запуска/остановки и режим терминала переезжают из кода
воркера и `services/queue.py` в данные — `launch_profiles` +
иммутабельные `launch_profile_versions`.

Сид — общий профиль по умолчанию (`department_id IS NULL`), им пользуются
все отделы, пока не заведут свой:

* `starter_script` — `emm/allta_app_full/starter.sh` 1:1, кроме строки
  клонирования (`starter.sh:56`): токен не аргументом `$2`, а из файла
  `{{GIT_TOKEN_PATH}}` (файл сразу удаляется), аргументы и URL клона — из
  настроек профиля (`{{GIT_CLONE_ARGS}}`, `{{GIT_REPO_URL}}`), по
  умолчанию дающие ровно легаси `--branch "$1" --single-branch
  https://git.astralinux.ru/scm/qa/stress_test.git` (без `--depth`);
* пути — легаси `/home/u/...` (`starter.sh:54`, `backup_image.py:291`)
  через `{TEST_HOME}` тестовой учётки;
* `launch_command_template` = `sudo bash {STARTER_PATH}` + значение
  переменной `STARTER_ARGS_TEMPLATE` (; легаси
  `backup_image.py:1034-1040`) — переменная больше не используется;
* остановка (T1): все потомки `starter.sh` по PPID, `kill -TERM`, через
  `stop_grace_seconds` (10) — `kill -KILL`, под `sudo`; легаси
  `pkill -f starter.sh` не трогал `run.py`;
* `use_pty = true` (T5, легаси `get_pty()` в `backup_image.py`);
* testenv (T2): маркер `on`/`off` пишется всегда, прочие `testenv_*.conf`
  не удаляются (сервер чистый после восстановления образа).

Права `launch_profile:view/update` — системной роли `admin` (как у тестовой
учётки, решение 24.09); department_admin своего отдела проходит мимо
матрицы.
"""
from typing import Sequence, Union
from uuid import uuid4

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c1f7a3e9d402"
down_revision: Union[str, None] = "b8e4f1a7c265"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_DEFAULT_PROFILE_ID = "lp_default"
_DEFAULT_VERSION_ID = "lpv_default_1"
_LEGACY_STARTER_ARGS = "{TEST_BRANCH} {GIT_TOKEN_FILE} {DATES_FILE} {RC_NAME} {STARTER_SUFFIX}"

STARTER_SCRIPT = '#!/bin/bash\n\nset -vx\n\n#\n#Аргументом скрипту следует указать ветку проекта\n#\nlocalhost=`hostname -I | awk \'{print $1}\'`\ncurrent_kernel=`uname -r`\ntestenv=`cat /home/u/testenv_*.conf`\ngit_directory="stress_test"\n\ncleanup_kernel() {\ninstalled_kernels=$(dpkg --list | grep \'linux-image-[0-9]\' | awk \'{print $2}\')\nfor kernel in $installed_kernels; do\n    if [[ "$kernel" != *"$current_kernel"* && "$kernel" != linux-image-5.10* ]]; then\n        echo "Removing $kernel..."\n        sudo apt remove --purge -y $kernel\n    fi\ndone\n\ninstalled_headers=$(dpkg --list | grep \'linux-headers-[0-9]\' | awk \'{print $2}\')\nfor header in $installed_headers; do\n    if [[ "$header" != *"$current_kernel"* && "$header" != linux-headers-5.10* ]]; then\n        echo "Removing $header..."\n        sudo apt remove --purge -y $header\n    fi\ndone\nsudo apt-get install linux-headers-$current_kernel -y\n\ninstalled_lam=$(dpkg --list | grep \'linux-astra-modules-[0-9]\' | awk \'{print $2}\')\nfor lam in $installed_lam; do\n    if [[ "$lam" != *"$current_kernel"* && "$lam" != linux-astra-modules-5.10* ]]; then\n        echo "Removing $lam..."\n        sudo apt remove --purge -y $lam\n    fi\ndone\n\n\necho "Cleaning up..."\nsudo apt autoremove -y\n}\n\necho $localhost\necho git bench = $1\n\n#Удаление неиспользуемых ядер\ncleanup_kernel\n\n#Предустановка пакетов\ndpkg -s sysstat &> /dev/null || sudo apt-get install sysstat -y\n\n#Клонируем репозиторий, удаляем старый, если есть\ncd /home/u/git\nsudo rm -r /home/u/git/stress_test\ngit -c http.extraHeader="Authorization: $(cat "{{GIT_TOKEN_PATH}}")" clone {{GIT_CLONE_ARGS}} {{GIT_REPO_URL}}; rm -f "{{GIT_TOKEN_PATH}}"\ncd "$git_directory"\ncd $1\n\n\necho sudo mkdir /etc/docker >> prepare.sh\ncat << \'EOF\' >> prepare.sh\ncat << INTERNAL_EOF > /etc/docker/daemon.json\n{\n  "insecure-registries": ["allta.devos.astralinux.ru:21503"]\n}\nINTERNAL_EOF\nEOF\necho sudo systemctl restart docker >> prepare.sh\n\necho curl http://10.177.103.10:18181/rest/api/dashboard/$localhost/full >> prepare.sh\necho sed -i \\\'s/.*cgroup_controllers.*/cgroup_controllers = [ \\"cpu\\", \\"devices\\", \\"memory\\", \\"blkio\\", \\"cpuacct\\" ]/g\\\' /etc/libvirt/qemu.conf >> prepare.sh\necho sudo systemctl restart libvirtd >> prepare.sh\nbash prepare.sh $1 $4 $6\n\nif [[ "$testenv" == \'on\' ]]; then\n    echo \'Подготовка тестового окружения завершена\'\n    exit 0\nelse\n    if [ "$5" == "kernel" ]; then\n        python3 run.py -n "$3" -kn "$5"\n    elif [ "$5" == "balance" ]; then\n        python3 run.py -n "$3" -bl "$5"\n    elif [ "$5" == "oom" ]; then\n        python3 run.py -n "$3" -oom "$5"\n    else\n        python3 run.py -n "$3"\n    fi\nfi\n'

STOP_COMMAND = 'sudo bash -c \'\ncollect() { for c in $(pgrep -P "$1"); do echo "$c"; collect "$c"; done; }\ntree() { for r in $(pgrep -f -- "$1"); do [ "$r" = "$$" ] && continue; echo "$r"; collect "$r"; done; }\npids=$(tree "{{STARTER_PGREP_PATTERN}}")\n[ -z "$pids" ] && exit 0\nkill -TERM $pids 2>/dev/null\nfor i in $(seq {{STOP_GRACE_SECONDS}}); do alive=""; for p in $pids; do kill -0 "$p" 2>/dev/null && alive="$alive $p"; done; [ -z "$alive" ] && exit 0; sleep 1; done\nkill -KILL $pids $(for p in $pids; do collect "$p"; done) 2>/dev/null\nexit 0\''

CLONE = {
    "repo_url": "https://git.astralinux.ru/scm/qa/stress_test.git",
    "mode": "branch",
    "depth": None,
    "credential": "git",
}

PATHS = {
    "script": "{TEST_HOME}/starter.sh",
    "dates": "{TEST_HOME}/dates_{QUEUE_ITEM_ID}.conf",
    "token": "{TEST_HOME}/git_token_{QUEUE_ITEM_ID}.conf",
    "testenv_marker": "{TEST_HOME}/testenv_marker.conf",
    "command_file": "{TEST_HOME}/command.txt",
}

TESTENV = {"on_value": "on", "off_value": "off", "cleanup_other": False}


def upgrade() -> None:
    op.create_table(
        "launch_profiles",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("department_id", sa.String(length=64), nullable=True),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("current_version_id", sa.String(length=64), nullable=True),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_launch_profiles_department_id", "launch_profiles", ["department_id"])
    # Один профиль по умолчанию на отдел (и один общий).
    op.create_index(
        "uq_launch_profiles_default", "launch_profiles", [sa.text("coalesce(department_id, '')")],
        unique=True, postgresql_where=sa.text("is_default"),
    )
    op.create_table(
        "launch_profile_versions",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column(
            "profile_id", sa.String(length=64),
            sa.ForeignKey("launch_profiles.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("comment", sa.String(length=512), nullable=True),
        sa.Column("starter_script", sa.Text(), nullable=False),
        sa.Column("clone", postgresql.JSONB(), nullable=False),
        sa.Column("paths", postgresql.JSONB(), nullable=False),
        sa.Column("launch_command_template", sa.Text(), nullable=False),
        sa.Column("stop_command_template", sa.Text(), nullable=False),
        sa.Column("stop_grace_seconds", sa.Integer(), nullable=False),
        sa.Column("use_pty", sa.Boolean(), nullable=False),
        sa.Column("testenv", postgresql.JSONB(), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("profile_id", "version", name="uq_launch_profile_versions_profile_version"),
    )
    op.create_index("ix_launch_profile_versions_profile_id", "launch_profile_versions", ["profile_id"])

    bind = op.get_bind()
    args = bind.execute(sa.text(
        "SELECT source_ref->>'value' FROM global_variables "
        "WHERE code = 'STARTER_ARGS_TEMPLATE' AND source = 'static'"
    )).scalar() or _LEGACY_STARTER_ARGS
    bind.execute(sa.text(
        "INSERT INTO launch_profiles (id, department_id, name, is_default, current_version_id) "
        "VALUES (:id, NULL, :name, true, :version_id)"
    ), {"id": _DEFAULT_PROFILE_ID, "name": "Легаси starter.sh", "version_id": _DEFAULT_VERSION_ID})
    versions = sa.table(
        "launch_profile_versions",
        sa.column("id", sa.String), sa.column("profile_id", sa.String), sa.column("version", sa.Integer),
        sa.column("comment", sa.String), sa.column("starter_script", sa.Text),
        sa.column("clone", postgresql.JSONB), sa.column("paths", postgresql.JSONB),
        sa.column("launch_command_template", sa.Text), sa.column("stop_command_template", sa.Text),
        sa.column("stop_grace_seconds", sa.Integer), sa.column("use_pty", sa.Boolean),
        sa.column("testenv", postgresql.JSONB),
    )
    op.bulk_insert(versions, [{
        "id": _DEFAULT_VERSION_ID, "profile_id": _DEFAULT_PROFILE_ID, "version": 1,
        "comment": "Сид миграции: легаси allta_app_full/starter.sh",
        "starter_script": STARTER_SCRIPT, "clone": CLONE, "paths": PATHS,
        "launch_command_template": f"sudo bash {{STARTER_PATH}} {args}",
        "stop_command_template": STOP_COMMAND, "stop_grace_seconds": 10, "use_pty": True,
        "testenv": TESTENV,
    }])
    bind.execute(sa.text(
        "UPDATE global_variables SET description = :d, updated_at = now() "
        "WHERE code = 'STARTER_ARGS_TEMPLATE'"
    ), {"d": "Не используется с: аргументы starter.sh — в launch_command_template профиля запуска."})

    op.add_column("test_definitions", sa.Column(
        "launch_profile_id", sa.String(length=64),
        sa.ForeignKey("launch_profiles.id", ondelete="SET NULL", name="fk_test_definitions_launch_profile"),
        nullable=True,
    ))
    op.add_column("queue_items", sa.Column("launch_profile_version_id", sa.String(length=64), nullable=True))
    op.add_column("department_test_settings", sa.Column(
        "log_chunk_interval_seconds", sa.Float(), nullable=False, server_default="2.5",
    ))
    op.add_column("department_test_settings", sa.Column(
        "log_chunk_max_bytes", sa.Integer(), nullable=False, server_default="4096",
    ))

    permissions = sa.table(
        "entity_permissions",
        sa.column("id", sa.String), sa.column("entity_type", sa.String),
        sa.column("role", sa.String), sa.column("action", sa.String),
    )
    op.bulk_insert(permissions, [
        {"id": f"prm_{uuid4().hex}", "entity_type": "launch_profile", "role": "admin", "action": action}
        for action in ("view", "update")
    ])


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM entity_permissions WHERE entity_type = 'launch_profile'"))
    op.drop_column("department_test_settings", "log_chunk_max_bytes")
    op.drop_column("department_test_settings", "log_chunk_interval_seconds")
    op.drop_column("queue_items", "launch_profile_version_id")
    op.drop_constraint("fk_test_definitions_launch_profile", "test_definitions", type_="foreignkey")
    op.drop_column("test_definitions", "launch_profile_id")
    op.drop_index("ix_launch_profile_versions_profile_id", table_name="launch_profile_versions")
    op.drop_table("launch_profile_versions")
    op.drop_index("uq_launch_profiles_default", table_name="launch_profiles")
    op.drop_index("ix_launch_profiles_department_id", table_name="launch_profiles")
    op.drop_table("launch_profiles")
