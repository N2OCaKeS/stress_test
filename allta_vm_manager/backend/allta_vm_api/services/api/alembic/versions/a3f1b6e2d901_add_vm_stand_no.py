"""add vm.stand_no

Revision ID: a3f1b6e2d901
Revises: 1d52ad4aac43
Create Date: 2026-05-19 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a3f1b6e2d901"
down_revision: Union[str, Sequence[str], None] = "1d52ad4aac43"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# DB hostname -> stand number for preset VMs.
# Keep in sync with PRESET_VMS in app/api/v1/routes/vm.py.
PRESET_STAND_NO = {
    "work-station1": 1,
    "work-station2": 2,
    "virtual-station1": 6,
    "virtual-station2": 7,
    "virtual-station3": 8,
    "virtual-station4": 9,
}


def upgrade() -> None:
    op.add_column("vm", sa.Column("stand_no", sa.Integer(), nullable=True))
    op.create_index(op.f("ix_vm_stand_no"), "vm", ["stand_no"], unique=False)

    vm = sa.table(
        "vm",
        sa.column("name", sa.String()),
        sa.column("stand_no", sa.Integer()),
    )
    for name, stand_no in PRESET_STAND_NO.items():
        op.execute(
            vm.update().where(vm.c.name == name).values(stand_no=stand_no)
        )


def downgrade() -> None:
    op.drop_index(op.f("ix_vm_stand_no"), table_name="vm")
    op.drop_column("vm", "stand_no")
