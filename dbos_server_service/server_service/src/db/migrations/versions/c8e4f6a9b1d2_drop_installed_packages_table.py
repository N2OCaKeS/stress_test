"""drop server_installed_packages table; switch to live SSH-probe

Revision ID: c8e4f6a9b1d2
Revises: b6f3a91d27e8
Create Date: 2026-05-22 06:00:00.000000

Установленные пакеты больше не хранятся в server_service'е. Вместо CRUD-
таблицы — единственный POST endpoint `/servers/{id}/installed-packages`,
который через worker делает SSH `dpkg-query` / `rpm -qa` к самому
серверу. Каждый запрос — live, БД-кэша нет.

Что делает миграция:

  1. drop table `server_installed_packages` + её indexes.
  2. чистим `entity_permissions` от записей с `entity_type='installed_package'`
     (admin/reader/operator грантов из seed-миграции `831ba55543e9` +
     никаких follow-on'ов: миграция `c7b41a9d2f08_add_installed_package_create_grants`
     удалена вместе с этим refactor'ом).

Downgrade воссоздаёт таблицу с тем же составом колонок, индексами и
UNIQUE-constraint'ом, и восстанавливает seed-гранты (admin: view/update/
delete; reader/operator: view; operator также update).
"""
from typing import Sequence, Union
from uuid import uuid4

import sqlalchemy as sa
from alembic import op


revision: str = "c8e4f6a9b1d2"
down_revision: Union[str, None] = "b6f3a91d27e8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. drop indexes + table.
    op.drop_index(
        op.f("ix_server_installed_packages_server_id"),
        table_name="server_installed_packages",
    )
    op.drop_index("ix_installed_package_name", table_name="server_installed_packages")
    op.drop_table("server_installed_packages")

    # 2. очистка entity_permissions.
    op.execute(
        "DELETE FROM entity_permissions WHERE entity_type = 'installed_package'"
    )


def downgrade() -> None:
    # 1. Восстановить таблицу.
    op.create_table(
        "server_installed_packages",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("server_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("version", sa.String(length=128), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=True),
        sa.Column("is_critical", sa.Boolean(), nullable=False),
        sa.Column(
            "discovered_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["server_id"], ["servers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "server_id", "name", "source",
            name="uq_installed_package_server_name_source",
        ),
    )
    op.create_index(
        "ix_installed_package_name",
        "server_installed_packages",
        ["name"],
        unique=False,
    )
    op.create_index(
        op.f("ix_server_installed_packages_server_id"),
        "server_installed_packages",
        ["server_id"],
        unique=False,
    )

    # 2. Восстановить seed-гранты под installed_package.
    table = sa.table(
        "entity_permissions",
        sa.column("id", sa.String),
        sa.column("entity_type", sa.String),
        sa.column("role", sa.String),
        sa.column("action", sa.String),
    )
    seed_rows: list[tuple[str, str]] = [
        # admin
        ("admin", "view"),
        ("admin", "update"),
        ("admin", "delete"),
        # reader
        ("reader", "view"),
        # operator
        ("operator", "view"),
        ("operator", "update"),
    ]
    op.bulk_insert(
        table,
        [
            {
                "id": f"prm_{uuid4().hex}",
                "entity_type": "installed_package",
                "role": role,
                "action": action,
            }
            for role, action in seed_rows
        ],
    )
