"""add severity to audit_events

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-04-19 01:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "b2c3d4e5f6a7"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "audit_events",
        sa.Column("severity", sa.String(16), nullable=False, server_default="INFO"),
    )
    op.create_index("ix_audit_events_severity", "audit_events", ["severity"])


def downgrade() -> None:
    op.drop_index("ix_audit_events_severity", table_name="audit_events")
    op.drop_column("audit_events", "severity")
