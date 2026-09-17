"""queue item interrupt action

Revision ID: c7e1b94a2d38
Revises: 2bf709abc3d2
Create Date: 2026-09-17 12:00:00.000000

Колонка под заявку на прерывание уже исполняющегося элемента очереди
(`skip`/`pause`). Заполняется публичными эндпоинтами `/queue-items/{id}/skip`
и `/queue-items/{id}/pause`, читается `testing_worker`ом через
`/internal/queue/{id}/interrupt-check` и сбрасывается в NULL, когда воркер
отчитался о прерывании.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c7e1b94a2d38"
down_revision: Union[str, None] = "2bf709abc3d2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "queue_items",
        sa.Column("interrupt_action", sa.String(length=16), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("queue_items", "interrupt_action")
