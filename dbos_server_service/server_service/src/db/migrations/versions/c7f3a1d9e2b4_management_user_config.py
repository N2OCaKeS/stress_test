"""management user config singleton

Revision ID: c7f3a1d9e2b4
Revises: e1c4a7d9f3b2
Create Date: 2026-06-24 00:00:00.000000

Платформенный singleton-конфиг управляющей учётки: имя управляющего
пользователя (`login`, дефолт `dbos`) и пер-режимные настройки bootstrap'а в
JSONB-колонке `modes` (группы + shell-команды на каждый режим создания учётки).
Одна строка на всю платформу, PK фиксирован значением `singleton`.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c7f3a1d9e2b4"
down_revision: Union[str, None] = "e1c4a7d9f3b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "management_user_config",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column(
            "login",
            sa.String(length=32),
            nullable=False,
            server_default="dbos",
        ),
        sa.Column(
            "modes",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("management_user_config")
