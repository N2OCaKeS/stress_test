"""server.management_mode detected at prepare

Revision ID: d2b8f1c6a9e3
Revises: c7f3a1d9e2b4
Create Date: 2026-06-24 10:00:00.000000

Воркер на этапе prepare определяет редакцию ОС на боксе и режим создания
управляющей учётки (`astra_orel`/`astra_smolensk`/`astra_voronezh`/`other_os`)
и возвращает его в prepared-callback'е. Колонка хранит детектнутый режим.
Nullable: до prepare режим неизвестен, старый воркер его не присылает.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d2b8f1c6a9e3"
down_revision: Union[str, None] = "c7f3a1d9e2b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "servers",
        sa.Column("management_mode", sa.String(length=32), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("servers", "management_mode")
