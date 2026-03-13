"""drop server ssh credentials from physical_servers

Revision ID: e7f41d9b2a6c
Revises: c3d9a7e8f4b2
Create Date: 2026-03-13 13:20:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "e7f41d9b2a6c"
down_revision: Union[str, Sequence[str], None] = "c3d9a7e8f4b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("physical_servers", "server_user")
    op.drop_column("physical_servers", "server_password")


def downgrade() -> None:
    op.add_column(
        "physical_servers",
        sa.Column("server_user", sa.String(length=100), nullable=False, server_default="root"),
    )
    op.add_column(
        "physical_servers",
        sa.Column("server_password", sa.String(), nullable=False, server_default=""),
    )
    op.alter_column("physical_servers", "server_user", server_default=None)
    op.alter_column("physical_servers", "server_password", server_default=None)
