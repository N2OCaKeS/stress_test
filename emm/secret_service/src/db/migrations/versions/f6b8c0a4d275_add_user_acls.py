"""add user_acls table

Per-user ACL для personal/department/cross_department-кред. Слой поверх
RoleACL: владелец personal-кред'ы выдаёт доступ поимённо конкретным user_id,
не плодя service-роли.

Revision ID: f6b8c0a4d275
Revises: e5a7b9f2c143
Create Date: 2026-06-15 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f6b8c0a4d275"
down_revision: Union[str, None] = "e5a7b9f2c143"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_acls",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("cred_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("can_read", sa.Boolean(), nullable=False),
        sa.Column("can_write", sa.Boolean(), nullable=False),
        sa.Column("granted_by_user_id", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["cred_id"], ["credentials.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("cred_id", "user_id", name="uq_user_acl_cred_user"),
    )
    op.create_index(
        op.f("ix_user_acls_cred_id"), "user_acls", ["cred_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_user_acls_cred_id"), table_name="user_acls")
    op.drop_table("user_acls")
