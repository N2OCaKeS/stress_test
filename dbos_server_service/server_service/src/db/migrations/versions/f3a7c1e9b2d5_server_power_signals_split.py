"""server split power signals (ping/ssh/ipmi) columns

Живая проба `power.status` теперь отдаёт три независимых сигнала доступности
одним callback'ом воркера: ping, ssh и питание по BMC (ipmi). Каждый пишется
в свою тройку колонок с собственным моментом приёма (UTC):

  * `ping_reachable` (bool) / `ping_latency_ms` (float) / `ping_checked_at`;
  * `ssh_reachable` (bool) / `ssh_latency_ms` (float) / `ssh_checked_at`;
  * `ipmi_power_state` (varchar 16) / `ipmi_checked_at`.

latency — в миллисекундах. Legacy-тройка `power_state`/`power_state_source`/
`power_state_checked_at` остаётся на месте и продолжает работать. Все колонки
nullable, backfill не нужен — у существующих строк NULL до первой пробы.

Downgrade: дроп восьми колонок.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f3a7c1e9b2d5"
down_revision: Union[str, None] = "e2b6f1a3c9d7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("servers", sa.Column("ping_reachable", sa.Boolean(), nullable=True))
    op.add_column("servers", sa.Column("ping_latency_ms", sa.Float(), nullable=True))
    op.add_column(
        "servers",
        sa.Column("ping_checked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("servers", sa.Column("ssh_reachable", sa.Boolean(), nullable=True))
    op.add_column("servers", sa.Column("ssh_latency_ms", sa.Float(), nullable=True))
    op.add_column(
        "servers",
        sa.Column("ssh_checked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "servers",
        sa.Column("ipmi_power_state", sa.String(length=16), nullable=True),
    )
    op.add_column(
        "servers",
        sa.Column("ipmi_checked_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("servers", "ipmi_checked_at")
    op.drop_column("servers", "ipmi_power_state")
    op.drop_column("servers", "ssh_checked_at")
    op.drop_column("servers", "ssh_latency_ms")
    op.drop_column("servers", "ssh_reachable")
    op.drop_column("servers", "ping_checked_at")
    op.drop_column("servers", "ping_latency_ms")
    op.drop_column("servers", "ping_reachable")
