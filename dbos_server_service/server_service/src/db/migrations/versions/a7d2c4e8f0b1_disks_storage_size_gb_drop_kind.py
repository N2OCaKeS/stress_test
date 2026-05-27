"""disks: size_bytes->size_gb, drop kind, remove disk grants

Revision ID: a7d2c4e8f0b1
Revises: c3f9b1a8d420
Create Date: 2026-05-27 10:00:00.000000

Диски переезжают внутрь карточки сервера (раздел `storage`), отдельного
CRUD-endpoint'а у них больше нет. Что делает миграция:

  1. server_disks.size_bytes -> size_gb (BigInteger). Существующие значения
     конвертируются целочисленно: size_gb = size_bytes / 1024^3 (floor).
     Диски меньше 1 ГБ дадут 0 — это осознанная потеря точности при переходе
     на гигабайтовую гранулярность.
  2. drop column server_disks.kind (hdd/ssd/nvme больше не хранится).
  3. чистим entity_permissions от записей с entity_type='disk' (seed-гранты
     admin/reader/operator из `831ba55543e9` — управление дисками идёт через
     server.create/update, отдельного disk-action в матрице нет).

Downgrade: восстанавливает колонку kind (nullable), переименовывает size_gb
обратно в size_bytes с обратной конвертацией (size_bytes = size_gb * 1024^3)
и пересевает disk-гранты (admin: view/create/update/delete; reader: view;
operator: view/create/update).
"""
from typing import Sequence, Union
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision: str = "a7d2c4e8f0b1"
down_revision: Union[str, None] = "c3f9b1a8d420"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_GIB = 1024 * 1024 * 1024


def upgrade() -> None:
    # 1. size_bytes -> size_gb с конвертацией значений.
    op.add_column(
        "server_disks",
        sa.Column("size_gb", sa.BigInteger(), nullable=True),
    )
    op.execute(f"UPDATE server_disks SET size_gb = size_bytes / {_GIB}")
    op.alter_column("server_disks", "size_gb", nullable=False)
    op.drop_column("server_disks", "size_bytes")

    # 2. kind больше не нужен.
    op.drop_column("server_disks", "kind")

    # 3. чистим матрицу прав от disk-грантов.
    op.execute("DELETE FROM entity_permissions WHERE entity_type = 'disk'")


def downgrade() -> None:
    # 1. вернуть kind (nullable, как в исходной схеме).
    op.add_column(
        "server_disks",
        sa.Column("kind", sa.String(length=32), nullable=True),
    )

    # 2. size_gb -> size_bytes с обратной конвертацией.
    op.add_column(
        "server_disks",
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
    )
    op.execute(f"UPDATE server_disks SET size_bytes = size_gb * {_GIB}")
    op.alter_column("server_disks", "size_bytes", nullable=False)
    op.drop_column("server_disks", "size_gb")

    # 3. пересеять disk-гранты.
    table = sa.table(
        "entity_permissions",
        sa.column("id", sa.String),
        sa.column("entity_type", sa.String),
        sa.column("role", sa.String),
        sa.column("action", sa.String),
    )
    seed_rows: list[tuple[str, str]] = [
        ("admin", "view"),
        ("admin", "create"),
        ("admin", "update"),
        ("admin", "delete"),
        ("reader", "view"),
        ("operator", "view"),
        ("operator", "create"),
        ("operator", "update"),
    ]
    op.bulk_insert(
        table,
        [
            {
                "id": f"prm_{uuid4().hex}",
                "entity_type": "disk",
                "role": role,
                "action": action,
            }
            for role, action in seed_rows
        ],
    )
