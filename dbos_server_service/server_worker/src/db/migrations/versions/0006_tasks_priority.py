"""tasks: priority column + priority-aware claim index

Revision ID: 0006_tasks_priority
Revises: 75abbe0d78aa
Create Date: 2026-06-24 00:00:00.000000

Заводит приоритет у worker-task'и. `priority` — int, больше = раньше: 0 —
обычный фон, 100 — high (срочный фан-аут, ручные power-операции). int, а не
enum, чтобы новый уровень не тянул alembic-миграцию вдогонку коду.

Индекс `ix_tasks_status_enqueued (status, enqueued_at)` заменяется на
`ix_tasks_status_priority_enqueued (status, priority DESC, enqueued_at)` —
claim/recovery теперь идёт «сначала приоритетные, при равенстве FIFO по
времени постановки». Тот же индекс обслуживает list-вьюхи по статусу.

server_default '0' покрывает legacy-row до миграции и dispatch'и без явного
priority.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006_tasks_priority"
down_revision: Union[str, None] = "75abbe0d78aa"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column(
            "priority",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.drop_index("ix_tasks_status_enqueued", table_name="tasks")
    op.create_index(
        "ix_tasks_status_priority_enqueued",
        "tasks",
        ["status", sa.text("priority DESC"), "enqueued_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_tasks_status_priority_enqueued", table_name="tasks")
    op.create_index(
        "ix_tasks_status_enqueued",
        "tasks",
        ["status", "enqueued_at"],
    )
    op.drop_column("tasks", "priority")
