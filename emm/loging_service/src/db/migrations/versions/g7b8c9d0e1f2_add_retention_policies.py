"""add retention_policies table

Revision ID: g7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-04-30 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "g7b8c9d0e1f2"
down_revision: Union[str, None] = "f6a7b8c9d0e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "retention_policies",
        sa.Column("id", sa.String(48), primary_key=True),
        sa.Column("severity", sa.String(16), nullable=True),   # NULL = all severities
        sa.Column("service", sa.String(64), nullable=True),    # NULL = all services
        sa.Column("retain_days", sa.Integer, nullable=False),
        sa.Column("description", sa.String(256), nullable=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_retention_policies_active", "retention_policies", ["is_active"])


def downgrade() -> None:
    op.drop_index("ix_retention_policies_active", table_name="retention_policies")
    op.drop_table("retention_policies")
