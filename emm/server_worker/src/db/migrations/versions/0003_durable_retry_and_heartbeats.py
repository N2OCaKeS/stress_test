"""durable retry scheduling + worker heartbeats

Revision ID: 0003_durable_retry_heartbeats
Revises: 0002_audit_outbox
Create Date: 2026-05-20 18:40:04.000000

Что добавляет миграция:

* `tasks.scheduled_retry_at` (nullable DateTime tz) — durable retry
  schedule: `_schedule_retry` пишет сюда «когда должен случиться re-kick»,
  при крэше worker'а startup-recovery поднимает row'ы с
  `scheduled_retry_at <= now() AND status='queued'`.
* `tasks.worker_id` (nullable String(64)) — какой воркер сейчас держит
  task'у. Заполняется в `mark_running`, читается sweep'ом для cross-
  replica zombie detection.
* `worker_heartbeats` — таблица heartbeat'ов: `worker_id PK,
  last_heartbeat_at`. UPSERT раз в ~60s через periodic-task.

Indexes:

* `ix_tasks_scheduled_retry` — partial index по
  `scheduled_retry_at IS NOT NULL`, ускоряет startup-scan.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003_durable_retry_heartbeats"
down_revision: Union[str, None] = "0002_audit_outbox"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── tasks: новые колонки ─────────────────────────────────────────────
    op.add_column(
        "tasks",
        sa.Column(
            "scheduled_retry_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "tasks",
        sa.Column("worker_id", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "ix_tasks_scheduled_retry",
        "tasks",
        ["scheduled_retry_at"],
        postgresql_where=sa.text("scheduled_retry_at IS NOT NULL"),
    )

    # ── worker_heartbeats: новая таблица ─────────────────────────────────
    op.create_table(
        "worker_heartbeats",
        sa.Column("worker_id", sa.String(length=64), nullable=False),
        sa.Column(
            "last_heartbeat_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("worker_id"),
    )


def downgrade() -> None:
    op.drop_table("worker_heartbeats")
    op.drop_index("ix_tasks_scheduled_retry", table_name="tasks")
    op.drop_column("tasks", "worker_id")
    op.drop_column("tasks", "scheduled_retry_at")
