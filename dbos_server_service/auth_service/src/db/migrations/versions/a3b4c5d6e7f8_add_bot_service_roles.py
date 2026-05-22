"""add bot_service_roles table

Revision ID: a3b4c5d6e7f8
Revises: f2a3b4c5d6e7
Create Date: 2026-05-14 00:30:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a3b4c5d6e7f8"
down_revision: Union[str, None] = "f2a3b4c5d6e7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "bot_service_roles",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("bot_id", sa.String(length=64), nullable=False),
        sa.Column("service_name", sa.String(length=128), nullable=False),
        sa.Column("role", sa.String(length=64), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "assigned_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("assigned_by", sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(["bot_id"], ["bot_accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["service_name"], ["platform_services.service_name"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("bot_id", "service_name", "role", name="uq_bot_service_role"),
    )
    op.create_index(
        "ix_bot_service_roles_bot_id", "bot_service_roles", ["bot_id"]
    )
    # Drop server_default once table exists so the app owns the value.
    op.alter_column("bot_service_roles", "is_active", server_default=None)


def downgrade() -> None:
    op.drop_index("ix_bot_service_roles_bot_id", table_name="bot_service_roles")
    op.drop_table("bot_service_roles")
