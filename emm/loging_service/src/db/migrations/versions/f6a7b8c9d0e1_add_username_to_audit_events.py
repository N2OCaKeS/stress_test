"""add username to audit_events

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-04-30 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "f6a7b8c9d0e1"
down_revision: Union[str, None] = "e5f6a7b8c9d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("audit_events", sa.Column("username", sa.String(128), nullable=True))
    op.create_index("ix_audit_events_username", "audit_events", ["username"])


def downgrade() -> None:
    op.drop_index("ix_audit_events_username", table_name="audit_events")
    op.drop_column("audit_events", "username")
