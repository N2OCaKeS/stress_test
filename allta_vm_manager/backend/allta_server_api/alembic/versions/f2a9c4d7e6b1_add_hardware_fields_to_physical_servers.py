"""add hardware fields to physical_servers

Revision ID: f2a9c4d7e6b1
Revises: e7f41d9b2a6c
Create Date: 2026-03-13 16:40:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "f2a9c4d7e6b1"
down_revision: Union[str, Sequence[str], None] = "e7f41d9b2a6c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("physical_servers", sa.Column("grade", sa.String(length=120), nullable=True))
    op.add_column("physical_servers", sa.Column("cpu_model", sa.String(length=255), nullable=True))
    op.add_column("physical_servers", sa.Column("cpu_cores_count", sa.Integer(), nullable=True))
    op.add_column("physical_servers", sa.Column("cpu_threads", sa.Integer(), nullable=True))
    op.add_column("physical_servers", sa.Column("storage", sa.String(length=255), nullable=True))
    op.add_column("physical_servers", sa.Column("gpu", sa.String(length=255), nullable=True))

    op.execute(
        """
        UPDATE physical_servers
        SET cpu_cores_count = cpu_total
        WHERE cpu_cores_count IS NULL
        """
    )
    op.execute(
        """
        UPDATE physical_servers
        SET cpu_threads = cpu_total
        WHERE cpu_threads IS NULL
        """
    )


def downgrade() -> None:
    op.drop_column("physical_servers", "gpu")
    op.drop_column("physical_servers", "storage")
    op.drop_column("physical_servers", "cpu_threads")
    op.drop_column("physical_servers", "cpu_cores_count")
    op.drop_column("physical_servers", "cpu_model")
    op.drop_column("physical_servers", "grade")
