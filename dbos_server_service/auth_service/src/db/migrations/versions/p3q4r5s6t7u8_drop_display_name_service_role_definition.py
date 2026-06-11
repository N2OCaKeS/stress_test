"""drop ServiceRoleDefinition.display_name

Revision ID: p3q4r5s6t7u8
Revises: o2p3q4r5s6t7
Create Date: 2026-06-11 12:03:00.000000

У ServiceRoleDefinition identifier — `role_name` (slug per dept/service).
`display_name` сносим: UI показывает `role_name` напрямую.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "p3q4r5s6t7u8"
down_revision: Union[str, None] = "o2p3q4r5s6t7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("service_role_definitions", "display_name")


def downgrade() -> None:
    op.add_column(
        "service_role_definitions",
        sa.Column("display_name", sa.String(length=256), nullable=True),
    )
    op.execute("UPDATE service_role_definitions SET display_name = role_name")
    op.alter_column("service_role_definitions", "display_name", nullable=False)
