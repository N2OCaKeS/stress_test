"""queue orchestration events

Revision ID: b7b483209bf8
Revises: bf3a95e0606e
Create Date: 2026-09-18 00:00:00.000000

Диагностический след диспетчера очереди — почему поллинг не привёл к
запуску теста (стенд занят вне testing_service, provisioning-запрос упал,
head-item завис, claim не нашёл ready-item). См. `models/queue_orchestration_
event.py` и `services/queue_orchestration_log.py`.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b7b483209bf8"
down_revision: Union[str, None] = "bf3a95e0606e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "queue_orchestration_events",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("stand_id", sa.String(length=64), nullable=False),
        sa.Column("queue_item_id", sa.String(length=64), nullable=True),
        sa.Column("kind", sa.String(length=48), nullable=False),
        sa.Column("detail", sa.String(length=2048), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["stand_id"], ["test_stands.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["queue_item_id"], ["queue_items.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_queue_orchestration_events_stand_id", "queue_orchestration_events", ["stand_id"],
    )
    op.create_index(
        "ix_queue_orchestration_events_queue_item_id", "queue_orchestration_events", ["queue_item_id"],
    )
    op.create_index(
        "ix_queue_orchestration_events_kind", "queue_orchestration_events", ["kind"],
    )
    op.create_index(
        "ix_queue_orchestration_events_created_at", "queue_orchestration_events", ["created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_queue_orchestration_events_created_at", table_name="queue_orchestration_events")
    op.drop_index("ix_queue_orchestration_events_kind", table_name="queue_orchestration_events")
    op.drop_index("ix_queue_orchestration_events_queue_item_id", table_name="queue_orchestration_events")
    op.drop_index("ix_queue_orchestration_events_stand_id", table_name="queue_orchestration_events")
    op.drop_table("queue_orchestration_events")
