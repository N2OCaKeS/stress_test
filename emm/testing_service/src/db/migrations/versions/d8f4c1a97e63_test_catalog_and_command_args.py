"""test catalog + command arg slots

Revision ID: d8f4c1a97e63
Revises: b1e7c4a9d203
Create Date: 2026-09-09 16:20:00.000000

Второй домен testing_service (§2.2, §3.2 плана миграции): каталог тестов
(`test_definitions`) и упорядоченные слоты конструктора команд
(`test_command_args`, литерал либо ссылка на `global_variables`).

`pinned_stand_id` на `test_definitions` — сырой id без FK: `test_stands`
появится отдельным доменом волной 4, привязка теста к стенду нужна уже
сейчас. `test_command_args.variable_id`, наоборот, настоящий FK — обе таблицы
уже живут в этой БД.

Права зоны `test_definition` сеются той же схемой, что и `global_variable` в
предыдущей миграции — системная роль `admin`, все четыре действия,
`department_id IS NULL`. Слоты команды отдельной матрицы не получают —
редактирование слота защищено `(test_definition, *, update)`.
"""
from typing import Sequence, Union
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision: str = "d8f4c1a97e63"
down_revision: Union[str, None] = "b1e7c4a9d203"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_TEST_DEFINITION_ACTIONS: list[str] = ["view", "create", "update", "delete"]


def upgrade() -> None:
    op.create_table(
        "test_definitions",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("full_name", sa.String(length=256), nullable=False),
        sa.Column("category", sa.String(length=64), nullable=True),
        sa.Column("owner", sa.String(length=128), nullable=True),
        sa.Column("readiness", sa.String(length=32), nullable=True),
        sa.Column("department_id", sa.String(length=64), nullable=True),
        sa.Column("pinned_stand_id", sa.String(length=64), nullable=True),
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
        "ix_test_definitions_code", "test_definitions", ["code"], unique=True,
    )
    op.create_index(
        "ix_test_definitions_department_id", "test_definitions", ["department_id"],
    )

    op.create_table(
        "test_command_args",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column(
            "test_id", sa.String(length=64),
            sa.ForeignKey("test_definitions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("literal_value", sa.Text(), nullable=True),
        sa.Column(
            "variable_id", sa.String(length=64),
            sa.ForeignKey("global_variables.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("override_value", sa.Text(), nullable=True),
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
        "ix_test_command_args_test_id", "test_command_args", ["test_id"],
    )
    op.create_index(
        "ix_test_command_args_variable_id", "test_command_args", ["variable_id"],
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
                "entity_type": "test_definition",
                "role": "admin",
                "action": action,
            }
            for action in _TEST_DEFINITION_ACTIONS
        ],
    )


def downgrade() -> None:
    op.execute(
        sa.text("DELETE FROM entity_permissions WHERE entity_type = 'test_definition'")
    )
    op.drop_index("ix_test_command_args_variable_id", table_name="test_command_args")
    op.drop_index("ix_test_command_args_test_id", table_name="test_command_args")
    op.drop_table("test_command_args")
    op.drop_index("ix_test_definitions_department_id", table_name="test_definitions")
    op.drop_index("ix_test_definitions_code", table_name="test_definitions")
    op.drop_table("test_definitions")
