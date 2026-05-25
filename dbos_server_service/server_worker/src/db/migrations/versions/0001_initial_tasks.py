"""initial: tasks table

Revision ID: 0001_initial
Revises:
Create Date: 2026-05-14 14:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tasks",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("task_kind", sa.String(length=64), nullable=False),
        sa.Column("target_server_id", sa.String(length=64), nullable=True),
        sa.Column("target_resource_id", sa.String(length=64), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "enqueued_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key", name="uq_task_idempotency_key"),
    )
    op.create_index("ix_tasks_task_kind", "tasks", ["task_kind"])
    op.create_index("ix_tasks_target_server_id", "tasks", ["target_server_id"])
    op.create_index("ix_tasks_kind_status", "tasks", ["task_kind", "status"])
    op.create_index("ix_tasks_status_enqueued", "tasks", ["status", "enqueued_at"])


def downgrade() -> None:
    op.drop_index("ix_tasks_status_enqueued", table_name="tasks")
    op.drop_index("ix_tasks_kind_status", table_name="tasks")
    op.drop_index("ix_tasks_target_server_id", table_name="tasks")
    op.drop_index("ix_tasks_task_kind", table_name="tasks")
    op.drop_table("tasks")
