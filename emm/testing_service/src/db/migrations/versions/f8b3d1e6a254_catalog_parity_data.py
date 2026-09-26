"""catalog parity data: short names, integration variables, postgresql.smolensk

Revision ID: f8b3d1e6a254
Revises: e4a7c2d9b813
Create Date: 2026-09-24 12:05:00.000000

Доводит уже импортированный каталог (`scripts/import_catalog.allta.yaml`) до
того же вида, что даёт свежий импорт после. Все шаги идемпотентны и
трогают только строки, совпадающие с исходным литералом каталога: слот,
который пользователь уже поменял, остаётся как есть.

1. Переменные источника `department_integration` (D2) — учётные данные
   конечных скриптов берутся из настроек интеграций отдела стенда:

   * `CONFLUENCE_USER` — login `confluence_credential_id` (пусто —
     `credential_id`); легаси `--username {tokens['username']}`
     (`emm/allta_app_full/backup_image.py:268,293`);
   * `CONFLUENCE_TOKEN` — secret той же учётки, sensitive; легаси
     `--token {tokens['conf_token']}` (`backup_image.py:267,294`);
   * `JIRA_BASIC_AUTH` — secret `credential_id`, sensitive; легаси
     `-ba "{tokens['jira_token']}"` (`backup_image.py:269,308`);
   * `CONFLUENCE_SPACE` — `stp_matrix_confluence_space`; легаси литерал
     `--confluence-space 'DEVQA'` (`backup_image.py:295`) — то же
     пространство, что легаси зашивал для СТП-матрицы.

   Вставка `ON CONFLICT (code) DO NOTHING`: заведённую руками переменную с
   тем же кодом миграция не перезаписывает.
2. `short_name` по `code` — значения легаси-словаря `tests`
   (`allta_image_conf.py:237-305`), только если поле пустое.
3. Литерал `none` сразу после `--username`/`--token`/`--confluence-space`/
   `-ba` становится ссылкой на переменную из п. 1. Слот с другим значением
   (пользователь уже вписал своё) не трогается.
4. `-fti`: у слота `FOLDER_TREE_ID` снимается `override_value = 'none'`.
   Значение даст источник `zephyr_folder`; до него — ошибка резолва
   `LAUNCH_CONTEXT_VARIABLE_MISSING`, если id не передан в `launch_context`.
5. `postgresql.smolensk` (T6): легаси `allta_back.py:29`
   (`'postgresql-sm': '-ps psql'`) ведёт в ветку `if args.PSQL:`
   (`backup_image.py:333-335`) — `-db -sn … -c --package postgresql-11`, а не
   `-fs postgresql-sm`. Пара слотов `-fs postgresql-sm` заменяется на `-db`,
   в конец добавляются `-c --package postgresql-11`; статус `development`
   (импортный `draft`) → `ready`.
"""
import uuid
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f8b3d1e6a254"
down_revision: Union[str, None] = "e4a7c2d9b813"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# (code, label, is_sensitive, source_ref, description)
_VARIABLES: list[tuple[str, str, bool, dict, str]] = [
    (
        "CONFLUENCE_USER", "Логин Confluence отдела", False,
        {"field": "confluence_credential_id", "fallback": "credential_id", "credential_part": "login"},
        "Легаси --username (backup_image.py:268,293): login учётки Confluence отдела стенда; "
        "без отдельной учётки Confluence — общей credential_id.",
    ),
    (
        "CONFLUENCE_TOKEN", "Токен Confluence отдела", True,
        {"field": "confluence_credential_id", "fallback": "credential_id", "credential_part": "secret"},
        "Легаси --token (backup_image.py:267,294): secret учётки Confluence отдела стенда.",
    ),
    (
        "JIRA_BASIC_AUTH", "Jira/Zephyr basic auth отдела", True,
        {"field": "credential_id", "credential_part": "secret"},
        "Легаси -ba (backup_image.py:269,308, tokens['jira_token']): secret учётки Jira/Zephyr отдела стенда.",
    ),
    (
        "CONFLUENCE_SPACE", "Пространство Confluence для отчётов", False,
        {"field": "stp_matrix_confluence_space"},
        "Легаси --confluence-space 'DEVQA' (backup_image.py:295): пространство Confluence отдела стенда.",
    ),
]

# Флаг → переменная, на которую переключается следующий за ним литерал `none`.
_PLACEHOLDER_SLOTS: dict[str, str] = {
    "--username": "CONFLUENCE_USER",
    "--token": "CONFLUENCE_TOKEN",
    "--confluence-space": "CONFLUENCE_SPACE",
    "-ba": "JIRA_BASIC_AUTH",
}
_PLACEHOLDER = "none"

# code → легаси-ключ словаря `tests` (allta_image_conf.py:237-305) по full_name теста.
_SHORT_NAMES: dict[str, str] = {
    "postgresql.base": "postgresql",
    "postgresql.vanilla": "psql vanilla",
    "postgresql.tantor_vanilla": "tantor vanilla",
    "postgresql.parsec": "psql parsec",
    "postgresql.audit_off": "postgresql-aud-off",
    "postgresql.smolensk": "postgresql-sm",
    "postgresql.balance": "psql balance",
    "postgresql.oom": "psql oom",
    "postgresql.info_sys": "psql info-sys",
    "postgresql.info_sys_orel": "psql info-sys-orel",
    "postgresql.kernels": "psql kernels",
    "postgresql.tantor_kernels": "tantor kernels",
    "postgresql.olap_hq": "PSQL OLAP-hq",
    "file_systems.ext4": "EXT4",
    "file_systems.xfs": "XFS",
    "file_systems.ntfs": "NTFS",
    "file_systems.ext3": "EXT3",
    "file_systems.ext2": "EXT2",
    "file_systems.fat": "FAT",
    "file_systems.exfat": "EXFAT",
    "file_systems.ext4_parsec": "EXT4 parsec",
    "file_systems.xfs_parsec": "XFS parsec",
    "cluster_file_systems.ocfs2": "OCFS2",
    "cluster_file_systems.ceph": "CEPH",
    "cluster_file_systems.ceph_fio": "CEPH fio",
    "cluster_file_systems.ceph_parsec": "CEPH parsec",
    "auditd.psaud": "auditd-p",
    "auditd.fileaud": "auditd-f",
    "auditd.useraud": "auditd-u",
    "syslog_ng.base": "syslog-ng",
    "syslog_ng.check_write_log": "syslog-ng-cwl",
    "linux_system.unixbench": "unix",
    "linux_system.unixbench_parsec": "unix parsec",
    "overflow.ram": "RAM-overflow",
    "overflow.storage_drive": "SD-overflow",
    "parsec.impact_fs": "parsec impact-fs",
    "parsec.impact_fs_audit_off": "parsec impact-fs aud-off",
    "parsec.digsig_cdt": "digsig-cdt",
    "parsec.raw_spin_lock": "raw-spin-lock",
    "apache2.reverse_proxy": "apache-rp",
    "apache2.bench_pam": "apache-bp",
    "apache2.balance": "apache-balance",
    "virt.steal_time": "steal time",
    "virt.steal_time_smolensk": "steal time-sm",
    "virt.fio": "FIO",
    "virt.fio_large": "FIO large",
    "virt.unixbench": "vUnixBench",
    "virt.pingpong": "vPingPong",
    "docker.web_application": "docker-wa",
    "astra_openvpn.client_connections": "AOpenVPNcc",
    "exim.dovecot_imap": "Dovecot-IMAP",
    "exim.exim_smtp": "Exim4-SMTP",
    "network.init_on_free": "InitOnFree",
    "network.dhcp": "DHCP",
    "kernel.segfault": "SegFault",
    "kernel.xfs_memleak": "XFS mem leak",
    "kernel.os_usage": "OS usage",
    "freeipa.auth": "FreeIPA auth",
    "freeipa.create_users": "FreeIPA c-users",
    "freeipa.plugin": "FreeIPA plugin",
    "astraevents.base": "astraevents",
    "astraevents.smolensk": "astraevents-sm",
}

_SMOLENSK_CODE = "postgresql.smolensk"
# Хвост команды ветки `if args.PSQL:` (backup_image.py:333-335, `-c {pack_sql}`).
_SMOLENSK_TAIL = ("-c", "--package", "postgresql-11")

# Слоты с предыдущим слотом того же теста (по `position`, затем `id`).
_WITH_PREV = (
    "SELECT id, test_id, position, kind, literal_value, variable_id, override_value, "
    "LAG(kind) OVER w AS prev_kind, LAG(literal_value) OVER w AS prev_literal, "
    "LEAD(kind) OVER w AS next_kind, LEAD(literal_value) OVER w AS next_literal, "
    "LEAD(id) OVER w AS next_id "
    "FROM test_command_args WINDOW w AS (PARTITION BY test_id ORDER BY position, id)"
)


def _variable_id(bind, code: str) -> str | None:
    return bind.execute(sa.text("SELECT id FROM global_variables WHERE code = :code"), {"code": code}).scalar()


def _seed_variables(bind) -> None:
    insert = sa.text(
        "INSERT INTO global_variables "
        "(id, code, label, source, value_type, choices_source, is_sensitive, description, source_ref) "
        "VALUES (:id, :code, :label, 'department_integration', 'string', NULL, :sensitive, "
        ":description, :source_ref) "
        "ON CONFLICT (code) DO NOTHING"
    ).bindparams(sa.bindparam("source_ref", type_=postgresql.JSONB))
    for code, label, sensitive, source_ref, description in _VARIABLES:
        bind.execute(insert, {
            "id": f"gvar_{code.lower()}", "code": code, "label": label, "sensitive": sensitive,
            "description": description, "source_ref": source_ref,
        })


def _fill_short_names(bind) -> None:
    stmt = sa.text(
        "UPDATE test_definitions SET short_name = :short_name, updated_at = now() "
        "WHERE code = :code AND (short_name IS NULL OR btrim(short_name) = '')"
    )
    for code, short_name in _SHORT_NAMES.items():
        bind.execute(stmt, {"code": code, "short_name": short_name})


def _link_placeholder_slots(bind) -> None:
    stmt = sa.text(
        "UPDATE test_command_args a SET kind = 'variable', literal_value = NULL, "
        "variable_id = :variable_id, override_value = NULL, updated_at = now() "
        f"FROM ({_WITH_PREV}) p "
        "WHERE a.id = p.id AND p.kind = 'literal' AND p.literal_value = :placeholder "
        "AND p.prev_kind = 'literal' AND p.prev_literal = :flag"
    )
    for flag, code in _PLACEHOLDER_SLOTS.items():
        variable_id = _variable_id(bind, code)
        if variable_id is None:
            continue
        bind.execute(stmt, {"variable_id": variable_id, "placeholder": _PLACEHOLDER, "flag": flag})


def _drop_folder_tree_placeholder(bind) -> None:
    variable_id = _variable_id(bind, "FOLDER_TREE_ID")
    if variable_id is None:
        return
    bind.execute(
        sa.text(
            "UPDATE test_command_args SET override_value = NULL, updated_at = now() "
            "WHERE kind = 'variable' AND variable_id = :variable_id AND override_value = :placeholder"
        ),
        {"variable_id": variable_id, "placeholder": _PLACEHOLDER},
    )


def _fix_smolensk(bind) -> None:
    test_id = bind.execute(
        sa.text("SELECT id FROM test_definitions WHERE code = :code"), {"code": _SMOLENSK_CODE},
    ).scalar()
    if test_id is None:
        return
    row = bind.execute(
        sa.text(
            f"SELECT id, next_id FROM ({_WITH_PREV}) p WHERE p.test_id = :test_id "
            "AND p.kind = 'literal' AND p.literal_value = '-fs' "
            "AND p.next_kind = 'literal' AND p.next_literal = 'postgresql-sm'"
        ),
        {"test_id": test_id},
    ).first()
    if row is None:
        # Уже исправлено (или слоты поменял пользователь) — не трогаем.
        return
    fs_id, value_id = row
    bind.execute(
        sa.text("UPDATE test_command_args SET literal_value = '-db', updated_at = now() WHERE id = :id"),
        {"id": fs_id},
    )
    bind.execute(sa.text("DELETE FROM test_command_args WHERE id = :id"), {"id": value_id})
    last = bind.execute(
        sa.text("SELECT COALESCE(MAX(position), -1) FROM test_command_args WHERE test_id = :test_id"),
        {"test_id": test_id},
    ).scalar()
    insert = sa.text(
        "INSERT INTO test_command_args (id, test_id, position, kind, literal_value) "
        "VALUES (:id, :test_id, :position, 'literal', :value)"
    )
    for offset, value in enumerate(_SMOLENSK_TAIL, start=1):
        bind.execute(insert, {
            "id": f"targ_{uuid.uuid4().hex}", "test_id": test_id, "position": last + offset, "value": value,
        })
    bind.execute(
        sa.text(
            "UPDATE test_definitions SET readiness = 'ready', updated_at = now() "
            "WHERE id = :test_id AND readiness = 'development'"
        ),
        {"test_id": test_id},
    )


def upgrade() -> None:
    bind = op.get_bind()
    _seed_variables(bind)
    _fill_short_names(bind)
    _link_placeholder_slots(bind)
    _drop_folder_tree_placeholder(bind)
    _fix_smolensk(bind)


def downgrade() -> None:
    """Обратно к плейсхолдерам `none` — только для слотов, которые выглядят так, как их оставил upgrade."""
    bind = op.get_bind()

    # 5. postgresql.smolensk: `-db` → `-fs postgresql-sm`, без хвоста `-c --package postgresql-11`.
    test_id = bind.execute(
        sa.text("SELECT id FROM test_definitions WHERE code = :code"), {"code": _SMOLENSK_CODE},
    ).scalar()
    if test_id is not None:
        slots = bind.execute(
            sa.text(
                "SELECT id, position, kind, literal_value FROM test_command_args "
                "WHERE test_id = :test_id ORDER BY position, id"
            ),
            {"test_id": test_id},
        ).all()
        tail = [s for s in slots[-len(_SMOLENSK_TAIL):] if s.kind == "literal"]
        db_slot = next((s for s in slots if s.kind == "literal" and s.literal_value == "-db"), None)
        if db_slot is not None and tuple(s.literal_value for s in tail) == _SMOLENSK_TAIL:
            bind.execute(
                sa.text("DELETE FROM test_command_args WHERE id IN :ids").bindparams(
                    sa.bindparam("ids", expanding=True),
                ),
                {"ids": [s.id for s in tail]},
            )
            bind.execute(
                sa.text(
                    "UPDATE test_command_args SET position = position + 1 "
                    "WHERE test_id = :test_id AND position > :position"
                ),
                {"test_id": test_id, "position": db_slot.position},
            )
            bind.execute(
                sa.text("UPDATE test_command_args SET literal_value = '-fs' WHERE id = :id"), {"id": db_slot.id},
            )
            bind.execute(
                sa.text(
                    "INSERT INTO test_command_args (id, test_id, position, kind, literal_value) "
                    "VALUES (:id, :test_id, :position, 'literal', 'postgresql-sm')"
                ),
                {"id": f"targ_{uuid.uuid4().hex}", "test_id": test_id, "position": db_slot.position + 1},
            )
            bind.execute(
                sa.text("UPDATE test_definitions SET readiness = 'development' WHERE id = :id AND readiness = 'ready'"),
                {"id": test_id},
            )

    # 4. `-fti FOLDER_TREE_ID` снова с плейсхолдером.
    fti_id = _variable_id(bind, "FOLDER_TREE_ID")
    if fti_id is not None:
        bind.execute(
            sa.text(
                "UPDATE test_command_args a SET override_value = :placeholder "
                f"FROM ({_WITH_PREV}) p "
                "WHERE a.id = p.id AND p.kind = 'variable' AND p.variable_id = :variable_id "
                "AND p.override_value IS NULL AND p.prev_kind = 'literal' AND p.prev_literal = '-fti'"
            ),
            {"placeholder": _PLACEHOLDER, "variable_id": fti_id},
        )

    # 3. Переменные интеграций → литерал `none`.
    for flag, code in _PLACEHOLDER_SLOTS.items():
        variable_id = _variable_id(bind, code)
        if variable_id is None:
            continue
        bind.execute(
            sa.text(
                "UPDATE test_command_args a SET kind = 'literal', literal_value = :placeholder, "
                "variable_id = NULL "
                f"FROM ({_WITH_PREV}) p "
                "WHERE a.id = p.id AND p.kind = 'variable' AND p.variable_id = :variable_id "
                "AND p.override_value IS NULL AND p.prev_kind = 'literal' AND p.prev_literal = :flag"
            ),
            {"placeholder": _PLACEHOLDER, "variable_id": variable_id, "flag": flag},
        )

    # 2. short_name — только значения, проставленные этой миграцией.
    for code, short_name in _SHORT_NAMES.items():
        bind.execute(
            sa.text("UPDATE test_definitions SET short_name = NULL WHERE code = :code AND short_name = :short_name"),
            {"code": code, "short_name": short_name},
        )

    # 1. Сид-переменные, на которые больше никто не ссылается.
    bind.execute(
        sa.text(
            "DELETE FROM global_variables WHERE code IN :codes AND created_by IS NULL "
            "AND id NOT IN (SELECT variable_id FROM test_command_args WHERE variable_id IS NOT NULL)"
        ).bindparams(sa.bindparam("codes", expanding=True)),
        {"codes": [code for code, *_ in _VARIABLES]},
    )
