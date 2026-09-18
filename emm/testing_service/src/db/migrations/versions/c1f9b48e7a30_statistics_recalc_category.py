"""statistics recalc category

Revision ID: c1f9b48e7a30
Revises: b9e4c7d21a58
Create Date: 2026-09-18 10:10:00.000000

Пересчёт статистики перестал быть только «всё сразу»: вернулись восемь
пер-категорийных триггеров легаси. Индикатор должен показывать, что именно
сейчас считается, иначе «идёт расчёт» не отличить от полного прогона.
NULL — полный пересчёт, как и было у всех уже существующих строк.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c1f9b48e7a30"
down_revision: Union[str, None] = "b9e4c7d21a58"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "statistics_recalc_status",
        sa.Column("category", sa.String(length=32), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("statistics_recalc_status", "category")
