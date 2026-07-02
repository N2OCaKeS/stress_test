"""server os_security_mode

Per-server режим безопасности Astra (Smolensk/Orel/Voronezh), детектнутый на
боксе и присланный в inventory-callback'е. Версия ОС живёт в общем каталоге
os_versions, а режим у каждого сервера свой — поэтому отдельная nullable-колонка
на servers, а не в каталоге. Без default: у существующих строк режим появится
на ближайшей инвентаризации.

Revision ID: d9a4c7e2f1b8
Revises: c5f2a9b1e7d4
Create Date: 2026-07-02
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d9a4c7e2f1b8"
down_revision: Union[str, None] = "c5f2a9b1e7d4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "servers",
        sa.Column("os_security_mode", sa.String(length=32), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("servers", "os_security_mode")
