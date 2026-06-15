"""add actor_ip and user_agent to audit_events

Revision ID: o5p6q7r8s9t0
Revises: n4o5p6q7r8s9
Create Date: 2026-06-15 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "o5p6q7r8s9t0"
down_revision: Union[str, None] = "n4o5p6q7r8s9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("audit_events", sa.Column("actor_ip", sa.String(64), nullable=True))
    op.add_column("audit_events", sa.Column("user_agent", sa.String(512), nullable=True))


def downgrade() -> None:
    op.drop_column("audit_events", "user_agent")
    op.drop_column("audit_events", "actor_ip")
