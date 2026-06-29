"""console_macros.group_name column

Необязательная группа внутри скоупа макроса: UI собирает макросы в
сворачиваемые секции (системные/собственные). Колонка nullable, backfill не
нужен — у существующих строк группа остаётся NULL (без группы).

Revision ID: f2a6c1d4b9e3
Revises: d8b3e1c7a5f2
Create Date: 2026-06-29
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f2a6c1d4b9e3"
down_revision: Union[str, None] = "d8b3e1c7a5f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "console_macros",
        sa.Column("group_name", sa.String(length=128), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("console_macros", "group_name")
