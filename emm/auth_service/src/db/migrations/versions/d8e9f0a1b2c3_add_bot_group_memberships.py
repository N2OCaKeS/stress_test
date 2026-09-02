"""add bot_group_memberships table

Revision ID: d8e9f0a1b2c3
Revises: c7d8e9f0a1b2
Create Date: 2026-05-28 12:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d8e9f0a1b2c3"
down_revision: Union[str, None] = "c7d8e9f0a1b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "bot_group_memberships",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("group_id", sa.String(length=64), nullable=False),
        sa.Column("bot_id", sa.String(length=64), nullable=False),
        sa.Column(
            "added_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("added_by", sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(["group_id"], ["user_groups.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["bot_id"], ["bot_accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("group_id", "bot_id", name="uq_bot_group_membership"),
    )
    op.create_index(
        "ix_bot_group_memberships_group_id", "bot_group_memberships", ["group_id"]
    )
    op.create_index(
        "ix_bot_group_memberships_bot_id", "bot_group_memberships", ["bot_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_bot_group_memberships_bot_id", table_name="bot_group_memberships")
    op.drop_index("ix_bot_group_memberships_group_id", table_name="bot_group_memberships")
    op.drop_table("bot_group_memberships")
