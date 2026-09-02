"""server_account_user_acl table + manage_account_acl grant

Прямой (per-account) ACL: строка раздаёт конкретному пользователю набор
действий на одну конкретную учётку, аддитивно к ролевой матрице. Управление
грантами гейтится новым action `(server_account, manage_account_acl)`:

* admin — получает: выдача прямого доступа на учётку это admin-операция.
* operator / reader — НЕ получают: узкий доступ раздаёт только admin (либо
  платформенный department_admin своего отдела через его admin-роль).
* worker_bot — не получает: гранты ведёт человек.

`console` (доступ к консоли учётки) добавлен в
`constants.ENTITY_ACTIONS[server_account]` как ролевой action; дефолтных
грантов на него миграция не сидит — он выдаётся либо прицельно per-account,
либо вручную через permission-эндпоинты.

Revision ID: c2e8b6f1a473
Revises: b2d7f4a9c1e6
Create Date: 2026-06-18
"""

from typing import Sequence, Union
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision: str = "c2e8b6f1a473"
down_revision: Union[str, None] = "b2d7f4a9c1e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_GRANTS: list[tuple[str, str, str]] = [
    ("server_account", "admin", "manage_account_acl"),
]

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

    # NOT EXISTS защищает от unique-violation на partial global index при
    # re-apply (тот же стиль, что у `831ba55543e9` / `b2d7f4a9c1e6`).
    for entity_type, role, action in _GRANTS:
        op.execute(
            sa.text(
                """
                INSERT INTO entity_permissions (id, entity_type, role, action, department_id)
                SELECT :id, :etype, :role, :action, NULL
                WHERE NOT EXISTS (
                    SELECT 1 FROM entity_permissions
                    WHERE entity_type = :etype
                      AND role = :role
                      AND action = :action
                      AND department_id IS NULL
                )
                """
            ).bindparams(
                id=f"prm_{uuid4().hex}",
                etype=entity_type,
                role=role,
                action=action,
            )
        )


def downgrade() -> None:
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
