"""vm inventory guest facts

Гостевые факты инвентаризации ВМ, присланные worker'ом в callback'е
`vm.inventory_sync`: версия ядра гостя (`uname -r`) и момент последнего
успешного приёма фактов. Версия ОС ВМ уже живёт свободной строкой в
`vms.os_version` (box-authoritative), отдельной колонки под неё не заводим.
Оба поля nullable без default: у существующих ВМ появятся на ближайшей
инвентаризации.

Revision ID: b3d7f1a29c84
Revises: a1f5c2d9e7b3
Create Date: 2026-07-10
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b3d7f1a29c84"
down_revision: Union[str, None] = "a1f5c2d9e7b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "vms",
        sa.Column("kernel", sa.String(length=256), nullable=True),
    )
    op.add_column(
        "vms",
        sa.Column("os_last_synced_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("vms", "os_last_synced_at")
    op.drop_column("vms", "kernel")
