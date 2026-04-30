"""add service_events table

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-04-28 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, None] = "c3d4e5f6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "service_events",
        sa.Column("id", sa.String(48), primary_key=True),
        sa.Column("service", sa.String(64), nullable=False),
        sa.Column("action", sa.String(128), nullable=False),
        sa.Column("description", sa.String(256), nullable=True),
        sa.Column("default_severity", sa.String(16), nullable=True),
        sa.Column("registered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("service", "action", name="uq_service_events_service_action"),
    )
    op.create_index("ix_service_events_service", "service_events", ["service"])


def downgrade() -> None:
    op.drop_index("ix_service_events_service", table_name="service_events")
    op.drop_table("service_events")
