"""scope service role definitions and user groups to department

Revision ID: f2a3b4c5d6e7
Revises: e1f2a3b4c5d6
Create Date: 2026-05-14 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f2a3b4c5d6e7"
down_revision: Union[str, None] = "e1f2a3b4c5d6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── service_role_definitions: scope by department, mark admin as system ──
    op.drop_constraint("uq_service_role_name", "service_role_definitions", type_="unique")

    op.add_column(
        "service_role_definitions",
        sa.Column("department_id", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "service_role_definitions",
        sa.Column("is_system", sa.Boolean(), nullable=False, server_default=sa.false()),
    )

    # Backfill: explode each existing role definition into per-department copies,
    # one row per active department that has access to the service. Drop the original
    # global rows afterwards. The seed `admin` rows are marked is_system=True.
    op.execute(
        """
        INSERT INTO service_role_definitions
            (id, department_id, service_name, role_name, display_name,
             description, is_active, is_system, created_at, created_by)
        SELECT
            substr(md5(random()::text || clock_timestamp()::text), 1, 24) AS id,
            dsa.department_id,
            srd.service_name,
            srd.role_name,
            srd.display_name,
            srd.description,
            srd.is_active,
            (srd.role_name = 'admin') AS is_system,
            srd.created_at,
            srd.created_by
        FROM service_role_definitions srd
        JOIN department_service_access dsa
          ON dsa.service_name = srd.service_name AND dsa.is_active = true
        WHERE srd.department_id IS NULL
        """
    )
    op.execute("DELETE FROM service_role_definitions WHERE department_id IS NULL")

    op.alter_column("service_role_definitions", "department_id", nullable=False)
    op.create_foreign_key(
        "fk_service_role_definitions_department_id",
        "service_role_definitions",
        "departments",
        ["department_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_service_role_definitions_department_id",
        "service_role_definitions",
        ["department_id"],
    )
    op.create_unique_constraint(
        "uq_dept_service_role_name",
        "service_role_definitions",
        ["department_id", "service_name", "role_name"],
    )
    # Drop server_default once backfill is done so application owns the value.
    op.alter_column("service_role_definitions", "is_system", server_default=None)

    # ── user_groups: bind to department ─────────────────────────────────────
    op.drop_constraint("uq_user_group_name", "user_groups", type_="unique")

    op.add_column(
        "user_groups",
        sa.Column("department_id", sa.String(length=64), nullable=True),
    )

    # Backfill: derive department_id from the first member. Drop groups that
    # have no members (cannot be bound to a department).
    op.execute(
        """
        UPDATE user_groups g
        SET department_id = u.department_id
        FROM user_group_memberships m
        JOIN users u ON u.id = m.user_id
        WHERE m.group_id = g.id
          AND u.department_id IS NOT NULL
          AND g.department_id IS NULL
          AND m.id = (
            SELECT m2.id FROM user_group_memberships m2
            WHERE m2.group_id = g.id ORDER BY m2.added_at ASC LIMIT 1
          )
        """
    )
    op.execute("DELETE FROM user_groups WHERE department_id IS NULL")

    op.alter_column("user_groups", "department_id", nullable=False)
    op.create_foreign_key(
        "fk_user_groups_department_id",
        "user_groups",
        "departments",
        ["department_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_user_groups_department_id",
        "user_groups",
        ["department_id"],
    )
    op.create_unique_constraint(
        "uq_dept_user_group_name",
        "user_groups",
        ["department_id", "name"],
    )


def downgrade() -> None:
    # ── user_groups ─────────────────────────────────────────────────────────
    op.drop_constraint("uq_dept_user_group_name", "user_groups", type_="unique")
    op.drop_index("ix_user_groups_department_id", table_name="user_groups")
    op.drop_constraint("fk_user_groups_department_id", "user_groups", type_="foreignkey")
    op.drop_column("user_groups", "department_id")
    op.create_unique_constraint("uq_user_group_name", "user_groups", ["name"])

    # ── service_role_definitions ────────────────────────────────────────────
    op.drop_constraint("uq_dept_service_role_name", "service_role_definitions", type_="unique")
    op.drop_index(
        "ix_service_role_definitions_department_id", table_name="service_role_definitions"
    )
    op.drop_constraint(
        "fk_service_role_definitions_department_id",
        "service_role_definitions",
        type_="foreignkey",
    )
    op.drop_column("service_role_definitions", "is_system")
    op.drop_column("service_role_definitions", "department_id")
    op.create_unique_constraint(
        "uq_service_role_name", "service_role_definitions", ["service_name", "role_name"]
    )
