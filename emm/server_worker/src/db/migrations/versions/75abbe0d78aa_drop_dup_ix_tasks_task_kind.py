"""drop dup ix tasks task kind

Revision ID: 75abbe0d78aa
Revises: m0n1o2p3q4r5
Create Date: 2026-06-05 12:15:49.091497

`ix_tasks_task_kind` дублирует prefix композита `ix_tasks_kind_status
(task_kind, status)`: любой `WHERE task_kind=?` идёт по композиту, отдельный
single-column index только добавляет write-overhead и autovacuum'у работу.
"""
from typing import Sequence, Union

from alembic import op


revision: str = '75abbe0d78aa'
down_revision: Union[str, None] = 'm0n1o2p3q4r5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_index("ix_tasks_task_kind", table_name="tasks")


def downgrade() -> None:
    op.create_index("ix_tasks_task_kind", "tasks", ["task_kind"])
