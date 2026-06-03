"""dispatch_outbox table

Revision ID: e7a4d951c3b2
Revises: c5a9b3d4e7f2
Create Date: 2026-06-03 21:00:00.000000

Transactional outbox для `dispatch_task`: закрывает race-окно между
commit'ом доменной транзакции и publish'ем в Redis. Сервис в той же
транзакции пишет outbox-строку, отдельный publisher (Phase C) дочитывает
`dispatched_at IS NULL` и шлёт в брокер.

Два partial-индекса:
* `ix_dispatch_outbox_pending` — основной poll: тянем `dispatched_at IS NULL`,
  сортируя по `created_at`;
* `ix_dispatch_outbox_retry` — отдельная выборка для backoff-ретраев, когда
  publisher уже падал и проставил `next_retry_at`.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e7a4d951c3b2"
down_revision: Union[str, None] = "c5a9b3d4e7f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "dispatch_outbox",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("task_id", sa.String(length=64), nullable=False),
        sa.Column("task_kind", sa.String(length=128), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "dispatched_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "attempts",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "next_retry_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_dispatch_outbox_task_id",
        "dispatch_outbox",
        ["task_id"],
        unique=False,
    )
    op.create_index(
        "ix_dispatch_outbox_pending",
        "dispatch_outbox",
        ["created_at"],
        unique=False,
        postgresql_where=sa.text("dispatched_at IS NULL"),
    )
    op.create_index(
        "ix_dispatch_outbox_retry",
        "dispatch_outbox",
        ["next_retry_at"],
        unique=False,
        postgresql_where=sa.text(
            "dispatched_at IS NULL AND next_retry_at IS NOT NULL"
        ),
    )


def downgrade() -> None:
    op.drop_index("ix_dispatch_outbox_retry", table_name="dispatch_outbox")
    op.drop_index("ix_dispatch_outbox_pending", table_name="dispatch_outbox")
    op.drop_index("ix_dispatch_outbox_task_id", table_name="dispatch_outbox")
    op.drop_table("dispatch_outbox")
