"""server disk mountpoints from inventory

Revision ID: e8f3a1c6d2b4
Revises: c3f7a1e9b4d6
Create Date: 2026-09-05
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e8f3a1c6d2b4"
down_revision: Union[str, None] = "c3f7a1e9b4d6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "server_disks",
        sa.Column("mountpoints", postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("server_disks", "mountpoints")
