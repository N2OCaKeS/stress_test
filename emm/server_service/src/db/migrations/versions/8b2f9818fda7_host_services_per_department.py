"""host services settings: platform singleton -> per-department, add unit list

Revision ID: 8b2f9818fda7
Revises: a9d3f7c2e6b1
Create Date: 2026-09-06 00:00:00.000000

The host-services feature landed one revision ago as a platform-wide
singleton gated to `account_admin`. The owner corrected the tenancy model
right after: each department has its own host (some have none at all), and
managing it belongs to that department's own admins, with `account_admin`
excluded entirely. `host_services_settings` has no real data in any deployed
environment yet, so this drops and recreates it with `department_id` as the
primary key instead of migrating the old singleton `id` column in place.
`host_service_units` is new: each department's own list of systemd unit
names to expose, replacing the hardcoded platform-wide allowlist.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "8b2f9818fda7"
down_revision: Union[str, None] = "a9d3f7c2e6b1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_table("host_services_settings")
    op.create_table(
        "host_services_settings",
        sa.Column("department_id", sa.String(length=64), primary_key=True),
        sa.Column("ssh_host", sa.String(length=255), nullable=True),
        sa.Column("ssh_port", sa.Integer(), nullable=False, server_default="22"),
        sa.Column("ssh_user", sa.String(length=64), nullable=True),
        sa.Column("ssh_private_key_encrypted", sa.Text(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_table(
        "host_service_units",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("department_id", sa.String(length=64), nullable=False),
        sa.Column("unit_name", sa.String(length=128), nullable=False),
        sa.Column("label", sa.String(length=128), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.UniqueConstraint(
            "department_id", "unit_name", name="uq_host_service_units_department_unit"
        ),
    )
    op.create_index(
        "ix_host_service_units_department_id", "host_service_units", ["department_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_host_service_units_department_id", table_name="host_service_units")
    op.drop_table("host_service_units")
    op.drop_table("host_services_settings")
    op.create_table(
        "host_services_settings",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("ssh_host", sa.String(length=255), nullable=True),
        sa.Column("ssh_port", sa.Integer(), nullable=False, server_default="22"),
        sa.Column("ssh_user", sa.String(length=64), nullable=True),
        sa.Column("ssh_private_key_encrypted", sa.Text(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
