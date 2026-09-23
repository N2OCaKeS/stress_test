"""account nopasswd sudo settings

Revision ID: ce6aafc8bdbf
Revises: ad5f3fcf1352
Create Date: 2026-09-23 00:00:00.000000

Per-department opt-in `account_nopasswd_sudo_settings`: when enabled, provision
of a sudo-`server_account` (`has_sudo=True`) on a server or VM also drops a
per-user `NOPASSWD: ALL` sudoers rule for that login (in addition to the
existing group-wide `sudo` membership), so routine test-automation sudo calls
on that department's test boxes stop prompting for a password. Default is
`False` on every department — current password-prompting behaviour is
unchanged until a department explicitly opts in.

By structure — a copy of `host_services_settings` (department_id as PK, one
flag per department, nothing else to enrich) rather than `acs_department_access`
(separate id + unique constraint) — there is exactly one row per department.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "ce6aafc8bdbf"
down_revision: Union[str, None] = "ad5f3fcf1352"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "account_nopasswd_sudo_settings",
        sa.Column("department_id", sa.String(length=64), primary_key=True),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column("created_by", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("account_nopasswd_sudo_settings")
