"""add ssh username to snapshot passwords

Revision ID: c3d9a7e8f4b2
Revises: b1f1a2d4c8e3
Create Date: 2026-03-12 14:10:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c3d9a7e8f4b2"
down_revision: Union[str, Sequence[str], None] = "b1f1a2d4c8e3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "snapshot_passwords",
        sa.Column("ssh_username", sa.String(length=100), nullable=False, server_default="u"),
    )
    op.alter_column("snapshot_passwords", "ssh_username", server_default=None)


def downgrade() -> None:
    op.drop_column("snapshot_passwords", "ssh_username")
