"""initial schema

Revision ID: a1b2c3d4e5f6
Revises:
Create Date: 2026-04-19 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "audit_events",
        sa.Column("id", sa.String(48), primary_key=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("service", sa.String(64), nullable=False),
        sa.Column("action", sa.String(128), nullable=False),
        sa.Column("actor_id", sa.String(48), nullable=True),
        sa.Column("actor_type", sa.String(32), nullable=False),
        sa.Column("department_id", sa.String(48), nullable=True),
        sa.Column("target_id", sa.String(48), nullable=True),
        sa.Column("target_type", sa.String(64), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("allowed", sa.Boolean, nullable=False),
        sa.Column("request_id", sa.String(64), nullable=True),
        sa.Column("details", JSONB, nullable=False, server_default="{}"),
    )

    op.create_index("ix_audit_events_timestamp", "audit_events", ["timestamp"])
    op.create_index("ix_audit_events_service", "audit_events", ["service"])
    op.create_index("ix_audit_events_action", "audit_events", ["action"])
    op.create_index("ix_audit_events_actor_id", "audit_events", ["actor_id"])
    op.create_index("ix_audit_events_department_id", "audit_events", ["department_id"])
    op.create_index("ix_audit_events_status", "audit_events", ["status"])
    op.create_index("ix_audit_events_allowed", "audit_events", ["allowed"])
    op.create_index(
        "ix_audit_events_service_timestamp",
        "audit_events",
        ["service", "timestamp"],
    )
    op.create_index(
        "ix_audit_events_department_timestamp",
        "audit_events",
        ["department_id", "timestamp"],
    )


def downgrade() -> None:
    op.drop_table("audit_events")
