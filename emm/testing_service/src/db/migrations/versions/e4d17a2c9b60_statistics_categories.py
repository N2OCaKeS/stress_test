"""statistics categories catalog + multi-category recalc status

Revision ID: e4d17a2c9b60
Revises: d6f2b8c4e1a9
Create Date: 2026-09-24 10:00:00.000000

Решение D18 (`TESTING_PARITY_PLAN.md`): список семейств для пер-категорийного
пересчёта статистики — не константа `statistics_client._CATEGORY_SPECS`, а
справочник в БД, редактируемый в настройках статистики. Сид ниже — ровно те
же восемь семейств, что были константой, в том же порядке, чтобы без ручных
действий получалось поведение allta_app:

* маршрут и `title_statistics` — из Flask-роутов легаси
  `emm/allta_app_full/allta_front.py` (строки указаны у каждой записи);
* `set_of_test_types`/`comparison_list`/`comparison_kernel_list` — из
  `emm/allta_app_full/statistics_conf.py` (строки указаны у каждой записи).

`Docker`/`Network` (`statistics_conf.py:33-38`) у внешнего сервиса есть, но
собственной кнопки в легаси не имели — не сеются, их можно завести из UI.

Право на запись справочника — то же `(statistics_settings, *, update)`, что
на настройки и ручной пересчёт; новых строк матрицы не нужно.

`statistics_recalc_status.categories` — весь набор семейств, выбранный в
модалке (несколько за один запуск), NULL — полный пересчёт.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "e4d17a2c9b60"
down_revision: Union[str, None] = "d6f2b8c4e1a9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# (key, label, path, title_statistics, set_of_test_types, comparison_list, comparison_kernel_list)
_SEED: list[tuple] = [
    # allta_front.py:730-745 (/base-statistics, 'Apache'); statistics_conf.py:2-4
    ("apache", "Apache", "/base-statistics", "Apache",
     ["apache-rp"], None, None),
    # allta_front.py:749-764 (/freeipa-statistics, 'FreeIPA'); statistics_conf.py:5-7
    ("freeipa", "FreeIPA", "/freeipa-statistics", "FreeIPA",
     ["FreeIPA auth", "FreeIPA c-users", "FreeIPA plugin"], None, None),
    # allta_front.py:767-783 (/parsec-statistics, 'Parsec'); statistics_conf.py:8-11
    ("parsec", "Parsec", "/parsec-statistics", "Parsec",
     ["parsec impact-fs", "parsec impact-fs aud-off", "raw-spin-lock", "digsig-cdt"],
     [["parsec impact-fs", "parsec impact-fs aud-off"]], None),
    # allta_front.py:787-804 (/postgresql-statistics, 'PostgreSQL'); statistics_conf.py:12-16
    ("postgresql", "PostgreSQL", "/postgresql-statistics", "PostgreSQL",
     ["postgresql", "postgresql-sm", "postgresql-aud-off", "psql parsec", "psql vanilla",
      "tantor vanilla", "psql balance", "PSQL OLAP-hq", "psql info-sys", "psql info-sys-orel"],
     [["postgresql", "postgresql-sm"], ["postgresql", "postgresql-aud-off"],
      ["postgresql", "psql parsec"], ["postgresql", "psql vanilla"],
      ["psql vanilla", "postgresql-aud-off"], ["psql info-sys", "psql info-sys-orel"]],
     ["postgresql"]),
    # allta_front.py:808-825 (/virt-statistics, 'Qemu/KVM/Libvirt'); statistics_conf.py:17-20
    ("virt", "Qemu/KVM/Libvirt", "/virt-statistics", "Qemu/KVM/Libvirt",
     ["FIO", "vPingPong", "vUnixBench", "steal time", "steal time-sm", "FIO large"],
     [["steal time", "steal time-sm"]], None),
    # allta_front.py:828-845 (/base-statistics, 'UnixBench'); statistics_conf.py:21-24
    ("unixbench", "UnixBench", "/base-statistics", "UnixBench",
     ["unix", "unix parsec"], [["unix", "unix parsec"]], None),
    # allta_front.py:848-865 (/base-statistics, 'Системные службы'); statistics_conf.py:25-28
    ("system_services", "Системные службы", "/base-statistics", "Системные службы",
     ["auditd-p", "auditd-f", "auditd-u", "syslog-ng", "AOpenVPNcc", "Dovecot-IMAP",
      "Exim4-SMTP", "astraevents", "astraevents-sm"],
     [["astraevents", "astraevents-sm"]], None),
    # allta_front.py:867-880 (/filesystems-statistics, 'Файловые системы'); statistics_conf.py:29-32
    ("filesystems", "Файловые системы", "/filesystems-statistics", "Файловые системы",
     ["EXFAT", "EXT2", "EXT4", "EXT4 parsec", "FAT", "NTFS", "XFS", "XFS parsec",
      "OCFS2", "CEPH", "CEPH fio"],
     [["EXT4", "XFS"], ["EXT4", "EXT4 parsec"]], None),
]


def upgrade() -> None:
    op.create_table(
        "statistics_categories",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("key", sa.String(length=32), nullable=False),
        sa.Column("label", sa.String(length=128), nullable=False),
        sa.Column("path", sa.String(length=128), nullable=False),
        sa.Column("title_statistics", sa.String(length=128), nullable=False),
        sa.Column("set_of_test_types", JSONB(), nullable=False, server_default="[]"),
        sa.Column("comparison_list", JSONB(none_as_null=True), nullable=True),
        sa.Column("comparison_kernel_list", JSONB(none_as_null=True), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.Column("created_by", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "ix_statistics_categories_key", "statistics_categories", ["key"], unique=True,
    )

    table = sa.table(
        "statistics_categories",
        sa.column("id", sa.String),
        sa.column("key", sa.String),
        sa.column("label", sa.String),
        sa.column("path", sa.String),
        sa.column("title_statistics", sa.String),
        sa.column("set_of_test_types", JSONB),
        sa.column("comparison_list", JSONB(none_as_null=True)),
        sa.column("comparison_kernel_list", JSONB(none_as_null=True)),
        sa.column("enabled", sa.Boolean),
        sa.column("sort_order", sa.Integer),
    )
    op.bulk_insert(
        table,
        [
            {
                "id": f"stcat_{key}",
                "key": key,
                "label": label,
                "path": path,
                "title_statistics": title,
                "set_of_test_types": types,
                "comparison_list": comparison,
                "comparison_kernel_list": kernel,
                "enabled": True,
                "sort_order": (index + 1) * 10,
            }
            for index, (key, label, path, title, types, comparison, kernel) in enumerate(_SEED)
        ],
    )

    op.add_column(
        "statistics_recalc_status",
        sa.Column("categories", JSONB(none_as_null=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("statistics_recalc_status", "categories")
    op.drop_index("ix_statistics_categories_key", table_name="statistics_categories")
    op.drop_table("statistics_categories")
