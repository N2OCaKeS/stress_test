"""queue item prepare only

Revision ID: bf3a95e0606e
Revises: f4a9d2c61b38
Create Date: 2026-09-18 00:00:00.000000

Флаг «только подготовить стенд» (легаси testenv-режим): при постановке
элемента очереди с этим флагом воркер кладёт на стенд testenv-маркер вместо
запуска теста. Терминальный исход такого item'а — `prepared`, отдельно от
обычных succeeded/failed.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "bf3a95e0606e"
down_revision: Union[str, None] = "f4a9d2c61b38"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "queue_items",
        sa.Column("prepare_only", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("queue_items", "prepare_only")
