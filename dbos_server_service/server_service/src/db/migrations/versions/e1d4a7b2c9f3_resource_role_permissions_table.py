"""resource_role_permissions table (instance-level ACL)

Revision ID: e1d4a7b2c9f3
Revises: a7e2f1c9d8b4
Create Date: 2026-06-29 12:00:00.000000

Инстанс-уровневый ACL поверх тип-wide матрицы entity_permissions: точечный
грант роли на КОНКРЕТНЫЙ ресурс (server / server_account).

Schema
------
* table ``resource_role_permissions`` (id, resource_type, resource_id, role,
  action, department_id?, granted_by, created_at, updated_at).
* lookup-index ``ix_resource_role_permissions_lookup``
  (resource_type, resource_id, role).
* два partial unique-индекса (как у entity_permissions):
    - ``uq_resource_role_permissions_global`` — (resource_type, resource_id,
      role, action) WHERE department_id IS NULL.
    - ``uq_resource_role_permissions_per_dept`` — (..., department_id) WHERE
      department_id IS NOT NULL.
* CHECK ``ck_resource_role_permissions_resource_type`` — resource_type IN
  ('server', 'server_account').
* CHECK ``ck_resource_role_permissions_granted_by_format`` — soft-FK формат
  (``usr_…`` / ``bot_…``), как у entity_permissions.granted_by.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e1d4a7b2c9f3"
down_revision: Union[str, None] = "a7e2f1c9d8b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_IDENTITY_REGEX = r"^(usr_|bot_)[A-Za-z0-9_-]+$"


def upgrade() -> None:
    op.create_table(
        "resource_role_permissions",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("resource_type", sa.String(length=64), nullable=False),
        sa.Column("resource_id", sa.String(length=64), nullable=False),
        sa.Column("role", sa.String(length=64), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("department_id", sa.String(length=64), nullable=True),
        sa.Column("granted_by", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "resource_type IN ('server', 'server_account')",
            name="ck_resource_role_permissions_resource_type",
        ),
        sa.CheckConstraint(
            f"granted_by IS NULL OR granted_by ~ '{_IDENTITY_REGEX}'",
            name="ck_resource_role_permissions_granted_by_format",
        ),
    )
    op.create_index(
        "ix_resource_role_permissions_lookup",
        "resource_role_permissions",
        ["resource_type", "resource_id", "role"],
        unique=False,
    )
    op.create_index(
        "ix_resource_role_permissions_role",
        "resource_role_permissions",
        ["role"],
        unique=False,
    )
    op.create_index(
        "uq_resource_role_permissions_global",
        "resource_role_permissions",
        ["resource_type", "resource_id", "role", "action"],
        unique=True,
        postgresql_where=sa.text("department_id IS NULL"),
    )
    op.create_index(
        "uq_resource_role_permissions_per_dept",
        "resource_role_permissions",
        ["resource_type", "resource_id", "role", "action", "department_id"],
        unique=True,
        postgresql_where=sa.text("department_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_resource_role_permissions_per_dept",
        table_name="resource_role_permissions",
    )
    op.drop_index(
        "uq_resource_role_permissions_global",
        table_name="resource_role_permissions",
    )
    op.drop_index(
        "ix_resource_role_permissions_role",
        table_name="resource_role_permissions",
    )
    op.drop_index(
        "ix_resource_role_permissions_lookup",
        table_name="resource_role_permissions",
    )
    op.drop_table("resource_role_permissions")
