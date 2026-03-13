"""add snapshot passwords table

Revision ID: 5c87df93a7f1
Revises: d4e7ccc2ce2f
Create Date: 2026-03-10 16:45:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "5c87df93a7f1"
down_revision: Union[str, Sequence[str], None] = "d4e7ccc2ce2f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "snapshot_passwords",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("snapshot_name", sa.String(length=120), nullable=False),
        sa.Column("password", sa.String(), nullable=False),
        sa.Column("updated_by", sa.String(length=120), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("snapshot_name"),
    )
    op.create_index(op.f("ix_snapshot_passwords_id"), "snapshot_passwords", ["id"], unique=False)
    op.create_index(
        op.f("ix_snapshot_passwords_snapshot_name"),
        "snapshot_passwords",
        ["snapshot_name"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_snapshot_passwords_snapshot_name"), table_name="snapshot_passwords")
    op.drop_index(op.f("ix_snapshot_passwords_id"), table_name="snapshot_passwords")
    op.drop_table("snapshot_passwords")
