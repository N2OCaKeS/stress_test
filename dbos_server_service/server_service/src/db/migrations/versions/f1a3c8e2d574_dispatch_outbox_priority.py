"""dispatch_outbox: priority column for priority-aware publish ordering

Revision ID: f1a3c8e2d574
Revises: d2b8f1c6a9e3
Create Date: 2026-06-24 00:00:00.000000

Заводит `priority` у outbox-строки — зеркало `tasks.priority`. Publisher в
воркере читает outbox `ORDER BY priority DESC, created_at ASC` и кладёт
high-priority-строки в отдельную Redis-очередь, которую воркер дренирует
первой. Без этой колонки outbox оставался строго FIFO по created_at, и
high-priority задача не обгоняла normal на первичной публикации.

server_default '0' покрывает legacy-row до миграции и dispatch'и без явного
priority. Partial-индекс по pending-строкам переезжает на
`(priority DESC, created_at)`, чтобы SELECT pending'а шёл по индексу.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f1a3c8e2d574"
down_revision: Union[str, None] = "d2b8f1c6a9e3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "dispatch_outbox",
        sa.Column(
            "priority",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.drop_index("ix_dispatch_outbox_pending", table_name="dispatch_outbox")
    op.create_index(
        "ix_dispatch_outbox_pending",
        "dispatch_outbox",
        [sa.text("priority DESC"), "created_at"],
        postgresql_where=sa.text("dispatched_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_dispatch_outbox_pending", table_name="dispatch_outbox")
    op.create_index(
        "ix_dispatch_outbox_pending",
        "dispatch_outbox",
        ["created_at"],
        postgresql_where=sa.text("dispatched_at IS NULL"),
    )
    op.drop_column("dispatch_outbox", "priority")
