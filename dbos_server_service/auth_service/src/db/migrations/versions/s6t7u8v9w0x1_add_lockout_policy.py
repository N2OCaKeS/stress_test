"""add lockout_policy (runtime-override таблица brute-force lockout-параметров)

Revision ID: s6t7u8v9w0x1
Revises: r5s6t7u8v9w0
Create Date: 2026-06-23 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "s6t7u8v9w0x1"
down_revision: Union[str, None] = "r5s6t7u8v9w0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Single-row override: пока строки нет — сервис тянет env-дефолты
    # (MAX_FAILED_LOGIN_ATTEMPTS / LOCKOUT_MINUTES). Намеренно НЕ сидим строку
    # здесь, чтобы свежий деплой стартовал на env-конфиге без миграции данных.
    op.create_table(
        "lockout_policy",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("max_failed_attempts", sa.Integer(), nullable=False),
        sa.Column("lockout_minutes", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_by", sa.String(length=64), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("lockout_policy")
