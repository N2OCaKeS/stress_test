"""entity_permissions department scope

Revision ID: abd8298d5349
Revises: 43cf9cfef9e1
Create Date: 2026-05-20 16:31:00.000000

Adds a nullable ``department_id`` column to ``entity_permissions`` so that
grants for *custom* roles can be scoped to a single department, while
grants for *built-in* roles stay system-wide. Closes the cross-department
privilege leak through colliding custom role names.

Schema changes
--------------

* ``ALTER TABLE entity_permissions ADD COLUMN department_id VARCHAR(64) NULL``
  + index ``ix_entity_permissions_department_id``.
* Drop the old global ``UNIQUE (entity_type, role, action)`` constraint
  (``uq_entity_role_action``) — replaced by two **partial unique indexes**:
    - ``uq_entity_permissions_global``: ``(entity_type, role, action)``
      WHERE ``department_id IS NULL`` — one system-wide row per triple.
    - ``uq_entity_permissions_per_dept``: ``(entity_type, role, action,
      department_id)`` WHERE ``department_id IS NOT NULL`` — one row per
      triple per department.

Backfill
--------

All existing rows currently come from two seed migrations
(``831ba55543e9_…`` and ``43cf9cfef9e1_…``) that write **only** built-in
roles (``admin``/``reader``/``operator``/``worker_bot``). Built-in roles
remain *system-wide*, so the backfill is a no-op (the new column defaults
to ``NULL``).

If a deployment has hand-written rows with custom roles that pre-date this
migration, they will retain ``department_id=NULL`` — i.e. they will keep
their pre-fix global semantics until an operator manually rescopes them.
This is **destructive only if** the operator intends those custom roles to
be per-dept; for the dev/test baseline the situation does not arise.

Downgrade
---------

Restores the original ``UNIQUE (entity_type, role, action)`` constraint.
Per-department rows that share a triple with another per-department row (or
with a system-wide row) cannot satisfy the global uniqueness — the
downgrade *will fail* on databases that have such collisions. Operators
have to resolve duplicates manually before downgrading. Drops the
``department_id`` column and its index.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "abd8298d5349"
down_revision: Union[str, None] = "43cf9cfef9e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Add the nullable department_id column.
    op.add_column(
        "entity_permissions",
        sa.Column("department_id", sa.String(length=64), nullable=True),
    )
    op.create_index(
        op.f("ix_entity_permissions_department_id"),
        "entity_permissions",
        ["department_id"],
        unique=False,
    )

    # 2. Drop the old global UNIQUE constraint that ignored department_id.
    op.drop_constraint(
        "uq_entity_role_action", "entity_permissions", type_="unique"
    )

    # 3. Two partial unique indexes — one for system-wide, one for per-dept.
    #    PostgreSQL supports `WHERE` in unique indexes; we rely on that here.
    op.create_index(
        "uq_entity_permissions_global",
        "entity_permissions",
        ["entity_type", "role", "action"],
        unique=True,
        postgresql_where=sa.text("department_id IS NULL"),
    )
    op.create_index(
        "uq_entity_permissions_per_dept",
        "entity_permissions",
        ["entity_type", "role", "action", "department_id"],
        unique=True,
        postgresql_where=sa.text("department_id IS NOT NULL"),
    )

    # 4. Backfill: explicit no-op. All current seeded rows are built-in roles
    #    (admin/reader/operator/worker_bot) and must remain system-wide; the
    #    new column defaults to NULL which is exactly what we want. The
    #    statement below documents intent and serves as a safety net should
    #    a previous custom-role seed have been hand-applied.
    op.execute(
        """
        -- backfill: built-in roles stay system-wide (department_id IS NULL).
        -- No UPDATE needed — column default is NULL.
        UPDATE entity_permissions
           SET department_id = NULL
         WHERE role IN ('guest', 'reader', 'operator', 'admin', 'worker_bot')
           AND department_id IS NOT NULL;
        """
    )


def downgrade() -> None:
    # 1. Drop the partial unique indexes.
    op.drop_index(
        "uq_entity_permissions_per_dept", table_name="entity_permissions"
    )
    op.drop_index(
        "uq_entity_permissions_global", table_name="entity_permissions"
    )

    # 2. Restore the global UNIQUE constraint. Will fail if rows still carry
    #    department_id and any (entity_type, role, action) is duplicated — an
    #    operator must dedupe by hand first.
    op.create_unique_constraint(
        "uq_entity_role_action",
        "entity_permissions",
        ["entity_type", "role", "action"],
    )

    # 3. Drop the column and its index.
    op.drop_index(
        op.f("ix_entity_permissions_department_id"),
        table_name="entity_permissions",
    )
    op.drop_column("entity_permissions", "department_id")
