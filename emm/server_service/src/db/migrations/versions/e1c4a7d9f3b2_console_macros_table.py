"""console_macros table (personal + system console macros)

Таблица сохранённых команд консоли. Скоуп в одной таблице:

* личный — is_system=false, user_id NOT NULL (владелец);
* системный — is_system=true, department_id NOT NULL, user_id NULL.

CHECK ck_console_macro_scope держит этот инвариант на уровне БД. Индексы:
по user_id (личные одного владельца) и составной (is_system, department_id)
под выдачу системных макросов отдела.

Revision ID: e1c4a7d9f3b2
Revises: d4f1a9c2e7b8
Create Date: 2026-06-24
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e1c4a7d9f3b2"
down_revision: Union[str, None] = "d4f1a9c2e7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "console_macros",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("command_text", sa.Text(), nullable=False),
        sa.Column(
            "display_order",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "is_system",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("user_id", sa.String(length=64), nullable=True),
        sa.Column("department_id", sa.String(length=64), nullable=True),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
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
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "(is_system = false AND user_id IS NOT NULL) "
            "OR (is_system = true AND user_id IS NULL AND department_id IS NOT NULL)",
            name="ck_console_macro_scope",
        ),
    )
    op.create_index(
        op.f("ix_console_macros_user_id"),
        "console_macros",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_console_macros_department_id"),
        "console_macros",
        ["department_id"],
        unique=False,
    )
    op.create_index(
        "ix_console_macros_system_dept",
        "console_macros",
        ["is_system", "department_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_console_macros_system_dept", table_name="console_macros")
    op.drop_index(
        op.f("ix_console_macros_department_id"), table_name="console_macros"
    )
    op.drop_index(op.f("ix_console_macros_user_id"), table_name="console_macros")
    op.drop_table("console_macros")
