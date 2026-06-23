"""drop server_account_user_acl + manage_account_acl grants

Пер-юзерный слой доступа к учёткам (прямые гранты на конкретную учётку) убран:
права раздаются только ролями (и наборами ролей в группах). Снимаем таблицу
`server_account_user_acl` и все seed-гранты `(server_account, *,
manage_account_acl)` — action управления этими грантами больше не существует.

Downgrade воссоздаёт таблицу с индексами и возвращает дефолтный
`(server_account, admin, manage_account_acl)` грант (как в исходной миграции
`c2e8b6f1a473`). Содержимое выданных грантов downgrade не восстанавливает —
оно теряется при DROP.

Revision ID: b9e1c7a3d206
Revises: a7d2f9c4e1b8
Create Date: 2026-06-23
"""

from typing import Sequence, Union
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision: str = "b9e1c7a3d206"
down_revision: Union[str, None] = "a7d2f9c4e1b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_ACL_COLUMNS = (
    "can_view",
    "can_view_password",
    "can_console",
    "can_update",
    "can_provision",
    "can_deprovision",
    "can_rotate_password",
    "can_delete",
    "can_grant_sudo",
)


def upgrade() -> None:
    op.execute(
        "DELETE FROM entity_permissions "
        "WHERE entity_type = 'server_account' AND action = 'manage_account_acl'"
    )
    op.drop_index(
        op.f("ix_server_account_user_acl_department_id"),
        table_name="server_account_user_acl",
    )
    op.drop_index(
        op.f("ix_server_account_user_acl_user_id"),
        table_name="server_account_user_acl",
    )
    op.drop_index(
        op.f("ix_server_account_user_acl_account_id"),
        table_name="server_account_user_acl",
    )
    op.drop_table("server_account_user_acl")


def downgrade() -> None:
    columns = [
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("account_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("department_id", sa.String(length=64), nullable=False),
    ]
    columns += [
        sa.Column(name, sa.Boolean(), server_default=sa.false(), nullable=False)
        for name in _ACL_COLUMNS
    ]
    columns += [
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["account_id"], ["server_accounts.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint("account_id", "user_id", name="uq_account_user_acl"),
    ]
    op.create_table("server_account_user_acl", *columns)
    op.create_index(
        op.f("ix_server_account_user_acl_account_id"),
        "server_account_user_acl",
        ["account_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_server_account_user_acl_user_id"),
        "server_account_user_acl",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_server_account_user_acl_department_id"),
        "server_account_user_acl",
        ["department_id"],
        unique=False,
    )
    op.execute(
        sa.text(
            """
            INSERT INTO entity_permissions (id, entity_type, role, action, department_id)
            SELECT :id, 'server_account', 'admin', 'manage_account_acl', NULL
            WHERE NOT EXISTS (
                SELECT 1 FROM entity_permissions
                WHERE entity_type = 'server_account'
                  AND role = 'admin'
                  AND action = 'manage_account_acl'
                  AND department_id IS NULL
            )
            """
        ).bindparams(id=f"prm_{uuid4().hex}")
    )
