"""inventory network interfaces, memory and disk usage

Инвентаризация научилась снимать сеть/память/занятость дисков:

* `servers.network_interfaces` — JSONB-список активных интерфейсов с бокса
  (основной остаётся в `network_interface_name`). NULL до первой инвентаризации.
* `server_disks.used_gb` / `server_disks.used_percent` — занятость диска с
  последней инвентаризации (сумма used всех его ФС из df). NULL, пока диск не
  смонтирован или df недоступен.

Объём ОЗУ (`servers.ram_total_mb`) колонка уже была — новая только заполняет её
из inventory, миграции под неё не нужно.

Revision ID: e2b6f1a3c9d7
Revises: d9a4c7e2f1b8
Create Date: 2026-07-03
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e2b6f1a3c9d7"
down_revision: Union[str, None] = "d9a4c7e2f1b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "servers",
        sa.Column("network_interfaces", postgresql.JSONB(), nullable=True),
    )
    op.add_column(
        "server_disks",
        sa.Column("used_gb", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "server_disks",
        sa.Column("used_percent", sa.Float(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("server_disks", "used_percent")
    op.drop_column("server_disks", "used_gb")
    op.drop_column("servers", "network_interfaces")
