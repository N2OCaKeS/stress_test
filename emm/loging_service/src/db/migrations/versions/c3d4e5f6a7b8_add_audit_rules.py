"""add audit_rules table

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-04-21 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, None] = "b2c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "audit_rules",
        sa.Column("id", sa.String(48), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("priority", sa.Integer, nullable=False, server_default="100"),
        # Match criteria
        sa.Column("match_service", sa.String(64), nullable=True),
        sa.Column("match_action", sa.String(128), nullable=True),
        sa.Column("match_status", sa.String(16), nullable=True),
        sa.Column("match_severity", sa.String(16), nullable=True),
        sa.Column("match_allowed", sa.Boolean, nullable=True),
        # Effect
        sa.Column("effect", sa.String(32), nullable=False),
        sa.Column("effect_severity", sa.String(16), nullable=True),
        # Timestamps
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("name", name="uq_audit_rules_name"),
    )
    op.create_index("ix_audit_rules_is_active_priority", "audit_rules", ["is_active", "priority"])


def downgrade() -> None:
    op.drop_index("ix_audit_rules_is_active_priority", table_name="audit_rules")
    op.drop_table("audit_rules")
