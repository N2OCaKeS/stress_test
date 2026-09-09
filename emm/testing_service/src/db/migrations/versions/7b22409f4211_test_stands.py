"""test stands

Revision ID: 7b22409f4211
Revises: d8f4c1a97e63
Create Date: 2026-09-09 18:00:00.000000

Третий домен testing_service (§2.3, §4 плана миграции): тестовые стенды —
надстройка над Server/Vm из server_service, без дублирования их полей.
`server_id` — сырой id без FK: межсервисная ссылка, тем же приёмом, что и
`test_definitions.pinned_stand_id` из предыдущей миграции. `UNIQUE(server_id)`
— один сервер не может быть двумя разными стендами одновременно.

Права зоны `test_stand` сеются той же схемой, что и `test_definition`/
`global_variable` — системная роль `admin`, все четыре действия,
`department_id IS NULL`.
"""
from typing import Sequence, Union
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision: str = "7b22409f4211"
down_revision: Union[str, None] = "d8f4c1a97e63"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_TEST_STAND_ACTIONS: list[str] = ["view", "create", "update", "delete"]


def upgrade() -> None:
    op.create_table(
        "test_stands",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("server_id", sa.String(length=64), nullable=False),
        sa.Column("department_id", sa.String(length=64), nullable=False),
        sa.Column("queue_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
    )
    op.create_index(
        "ix_test_stands_server_id", "test_stands", ["server_id"], unique=True,
    )
    op.create_index(
        "ix_test_stands_department_id", "test_stands", ["department_id"],
    )

    permissions = sa.table(
        "entity_permissions",
        sa.column("id", sa.String),
        sa.column("entity_type", sa.String),
        sa.column("role", sa.String),
        sa.column("action", sa.String),
    )
    op.bulk_insert(
        permissions,
        [
            {
                "id": f"prm_{uuid4().hex}",
                "entity_type": "test_stand",
                "role": "admin",
                "action": action,
            }
            for action in _TEST_STAND_ACTIONS
        ],
    )


def downgrade() -> None:
    op.execute(
        sa.text("DELETE FROM entity_permissions WHERE entity_type = 'test_stand'")
    )
    op.drop_index("ix_test_stands_department_id", table_name="test_stands")
    op.drop_index("ix_test_stands_server_id", table_name="test_stands")
    op.drop_table("test_stands")
