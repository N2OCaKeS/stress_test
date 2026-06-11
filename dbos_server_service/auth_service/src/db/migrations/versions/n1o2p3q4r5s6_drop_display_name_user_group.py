"""collapse UserGroup.display_name into name

Revision ID: n1o2p3q4r5s6
Revises: m0n1o2p3q4r5
Create Date: 2026-06-11 12:01:00.000000

Симметрично departments: `user_groups.display_name` переезжает в `name`,
колонка сносится. UNIQUE(`department_id`, `name`) сохраняется как есть.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "n1o2p3q4r5s6"
down_revision: Union[str, None] = "m0n1o2p3q4r5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "UPDATE user_groups "
        "SET name = display_name "
        "WHERE display_name IS NOT NULL AND TRIM(display_name) != ''"
    )
    op.drop_column("user_groups", "display_name")


def downgrade() -> None:
    op.add_column(
        "user_groups",
        sa.Column("display_name", sa.String(length=256), nullable=True),
    )
    op.execute("UPDATE user_groups SET display_name = name")
    op.alter_column("user_groups", "display_name", nullable=False)
