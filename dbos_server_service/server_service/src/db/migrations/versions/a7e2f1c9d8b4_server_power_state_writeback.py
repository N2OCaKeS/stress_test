"""server power_state writeback columns

Воркер после живой пробы питания (`power.status`) пишет результат обратно
в кэш сервера через internal-callback. Две новые колонки фиксируют источник
и момент пробы, чтобы UI показывал «(по ssh, 5 мин назад)»:

  * `servers.power_state_source` (varchar 16, nullable) — bmc/ping/ssh;
  * `servers.power_state_checked_at` (timestamptz, nullable) — момент приёма
    результата (UTC).

Само поле `servers.power_state` уже существует. Обе колонки nullable, backfill
не нужен — у существующих строк остаются NULL (пробы ещё не было).

Downgrade: дроп обеих колонок.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a7e2f1c9d8b4"
down_revision: Union[str, None] = "f2a6c1d4b9e3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("servers", sa.Column("power_state_source", sa.String(length=16), nullable=True))
    op.add_column(
        "servers",
        sa.Column("power_state_checked_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("servers", "power_state_checked_at")
    op.drop_column("servers", "power_state_source")
