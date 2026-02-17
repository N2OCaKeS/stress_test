"""drop legacy is_admin column

Revision ID: c4f7e1a9d2ab
Revises: 09c5d6b2f3e1
Create Date: 2026-02-13 21:35:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c4f7e1a9d2ab"
down_revision: Union[str, Sequence[str], None] = "09c5d6b2f3e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Safety backfill before dropping legacy flag.
    op.execute(
        """
        UPDATE users u
        SET role_id = r.id
        FROM roles r
        WHERE u.is_admin = TRUE
          AND r.name = 'admin';
        """
    )
    op.drop_column("users", "is_admin")


def downgrade() -> None:
    op.add_column(
        "users",
        sa.Column("is_admin", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.execute(
        """
        UPDATE users u
        SET is_admin = TRUE
        FROM roles r
        WHERE u.role_id = r.id
          AND r.name = 'admin';
        """
    )
    op.alter_column("users", "is_admin", server_default=None)
