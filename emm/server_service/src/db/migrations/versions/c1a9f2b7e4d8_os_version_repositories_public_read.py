"""os_version repositories column + drop os_version view grants

Revision ID: c1a9f2b7e4d8
Revises: a7d2c4e8f0b1
Create Date: 2026-05-27 14:23:00.000000

Две правки сущности `os_version`:

1. **Добавляет** колонку `os_versions.repositories` — массив URL-строк
   (`ARRAY(String)`), NOT NULL, дефолт пустой массив. Под список
   репозиториев версии (apt/yum/...).

2. **Удаляет** все гранты `(os_version, view)` из `entity_permissions`.
   Чтение каталога ОС стало публичным (list / get по id / get по имени
   без авторизации), action `view` для `os_version` больше не нужен.
   Подметаем по entity_type+action в один DELETE — независимо от роли
   (seed раздавал view роли admin/reader/operator) и department_id.

Downgrade воссоздаёт view-гранты по составу из seed-миграции
`831ba55543e9` (admin/reader/operator) и снимает колонку.
"""
from typing import Sequence, Union
from uuid import uuid4

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c1a9f2b7e4d8"
down_revision: Union[str, None] = "a7d2c4e8f0b1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "os_versions",
        sa.Column(
            "repositories",
            postgresql.ARRAY(sa.String()),
            nullable=False,
            server_default="{}",
        ),
    )

    op.execute(
        "DELETE FROM entity_permissions "
        "WHERE entity_type = 'os_version' AND action = 'view'"
    )


def downgrade() -> None:
    table = sa.table(
        "entity_permissions",
        sa.column("id", sa.String),
        sa.column("entity_type", sa.String),
        sa.column("role", sa.String),
        sa.column("action", sa.String),
    )
    op.bulk_insert(
        table,
        [
            {
                "id": f"prm_{uuid4().hex}",
                "entity_type": "os_version",
                "role": role,
                "action": "view",
            }
            for role in ("admin", "reader", "operator")
        ],
    )

    op.drop_column("os_versions", "repositories")
