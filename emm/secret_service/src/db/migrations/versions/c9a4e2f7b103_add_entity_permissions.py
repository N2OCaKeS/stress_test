"""add entity_permissions table

Тип-wide матрица прав secret_service: роль × action на сущность `secret`.
Служит базовым слоем allow для неличных секретов отдела (см.
`access_service._matrix_allows`). Уникальность — двумя partial unique-индексами
(system-wide vs per-department), как в server_service.

Revision ID: c9a4e2f7b103
Revises: b8d1f0a3c692
Create Date: 2026-07-01 10:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c9a4e2f7b103"
down_revision: Union[str, None] = "b8d1f0a3c692"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "entity_permissions",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("entity_type", sa.String(length=64), nullable=False),
        sa.Column("role", sa.String(length=64), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("department_id", sa.String(length=64), nullable=True),
        sa.Column("granted_by", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_entity_permissions_entity_type"),
        "entity_permissions",
        ["entity_type"],
        unique=False,
    )
    op.create_index(
        op.f("ix_entity_permissions_role"),
        "entity_permissions",
        ["role"],
        unique=False,
    )
    # Partial unique-индексы: одна system-wide строка на тройку и одна
    # per-department строка на тройку+отдел.
    op.create_index(
        "uq_secret_entity_permissions_global",
        "entity_permissions",
        ["entity_type", "role", "action"],
        unique=True,
        postgresql_where=sa.text("department_id IS NULL"),
    )
    op.create_index(
        "uq_secret_entity_permissions_per_dept",
        "entity_permissions",
        ["entity_type", "role", "action", "department_id"],
        unique=True,
        postgresql_where=sa.text("department_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_secret_entity_permissions_per_dept", table_name="entity_permissions"
    )
    op.drop_index(
        "uq_secret_entity_permissions_global", table_name="entity_permissions"
    )
    op.drop_index(
        op.f("ix_entity_permissions_role"), table_name="entity_permissions"
    )
    op.drop_index(
        op.f("ix_entity_permissions_entity_type"), table_name="entity_permissions"
    )
    op.drop_table("entity_permissions")
