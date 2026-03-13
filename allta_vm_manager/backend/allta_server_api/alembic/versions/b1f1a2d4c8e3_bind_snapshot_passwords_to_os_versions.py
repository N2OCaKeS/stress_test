"""bind snapshot passwords to os versions

Revision ID: b1f1a2d4c8e3
Revises: 5c87df93a7f1
Create Date: 2026-03-12 12:30:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b1f1a2d4c8e3"
down_revision: Union[str, Sequence[str], None] = "5c87df93a7f1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("snapshot_passwords", sa.Column("os_version_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_snapshot_passwords_os_version_id_os_versions",
        "snapshot_passwords",
        "os_versions",
        ["os_version_id"],
        ["id"],
        ondelete="CASCADE",
    )

    # Preserve existing rows: if OS version with same name does not exist yet, create it.
    op.execute(
        """
        INSERT INTO os_versions (name)
        SELECT DISTINCT sp.snapshot_name
        FROM snapshot_passwords sp
        LEFT JOIN os_versions ov ON ov.name = sp.snapshot_name
        WHERE ov.id IS NULL
        """
    )
    op.execute(
        """
        UPDATE snapshot_passwords sp
        SET os_version_id = ov.id
        FROM os_versions ov
        WHERE ov.name = sp.snapshot_name
        """
    )

    op.alter_column("snapshot_passwords", "os_version_id", nullable=False)

    op.drop_index(op.f("ix_snapshot_passwords_snapshot_name"), table_name="snapshot_passwords")
    op.create_index(
        op.f("ix_snapshot_passwords_os_version_id"),
        "snapshot_passwords",
        ["os_version_id"],
        unique=True,
    )
    op.drop_column("snapshot_passwords", "snapshot_name")


def downgrade() -> None:
    op.add_column("snapshot_passwords", sa.Column("snapshot_name", sa.String(length=120), nullable=True))
    op.execute(
        """
        UPDATE snapshot_passwords sp
        SET snapshot_name = ov.name
        FROM os_versions ov
        WHERE ov.id = sp.os_version_id
        """
    )
    op.alter_column("snapshot_passwords", "snapshot_name", nullable=False)
    op.create_index(
        op.f("ix_snapshot_passwords_snapshot_name"),
        "snapshot_passwords",
        ["snapshot_name"],
        unique=True,
    )

    op.drop_index(op.f("ix_snapshot_passwords_os_version_id"), table_name="snapshot_passwords")
    op.drop_constraint(
        "fk_snapshot_passwords_os_version_id_os_versions",
        "snapshot_passwords",
        type_="foreignkey",
    )
    op.drop_column("snapshot_passwords", "os_version_id")
