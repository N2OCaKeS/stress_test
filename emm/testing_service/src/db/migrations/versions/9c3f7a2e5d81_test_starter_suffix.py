"""test starter suffix

Revision ID: 9c3f7a2e5d81
Revises: d1a5e4b7c920
Create Date: 2026-09-11 00:00:00.000000

`test_definitions.starter_suffix` — позиционный `$5` у legacy `starter.sh`
("kernel"/"balance"/"oom"/пусто). Часть пересмотра архитектуры запуска теста:
`command` слотов теперь описывает содержимое `dates.conf`, а не argv
конечного скрипта — сам скрипт вызывает `starter.sh` на стенде, которому
нужен этот суффикс отдельным позиционным аргументом (см. `services/queue.py`).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "9c3f7a2e5d81"
down_revision: Union[str, None] = "d1a5e4b7c920"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "test_definitions",
        sa.Column("starter_suffix", sa.String(length=16), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("test_definitions", "starter_suffix")
