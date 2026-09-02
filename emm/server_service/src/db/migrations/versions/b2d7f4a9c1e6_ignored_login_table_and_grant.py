"""server_account_ignored_login table + manage_ignored_logins grant

Создаёт таблицу ignore-list'а логинов (скоуп — отдел) и сидит дефолтный
grant на новый action `(server_account, manage_ignored_logins)`:

* admin — полный доступ (как ко всем server_account-action'ам).
* operator — получает: управление ignore-list'ом — это update-уровень
  (правит, что инвентаризация считает незнакомым), оператор и так держит
  `update`/`create` на server_account.
* reader — НЕ получает: инвариант «reader только view».
* worker_bot — не получает: ignore-list ведёт человек, не воркер.

Revision ID: b2d7f4a9c1e6
Revises: a1c5e9f3b7d2
Create Date: 2026-06-17
"""

from typing import Sequence, Union
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision: str = "b2d7f4a9c1e6"
down_revision: Union[str, None] = "a1c5e9f3b7d2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_GRANTS: list[tuple[str, str, str]] = [
    ("server_account", "admin", "manage_ignored_logins"),
    ("server_account", "operator", "manage_ignored_logins"),
]


def upgrade() -> None:
    op.create_table(
        "server_account_ignored_login",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("department_id", sa.String(length=64), nullable=False),
        sa.Column("login", sa.String(length=128), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "department_id", "login", name="uq_ignored_login_dept_login"
        ),
    )
    op.create_index(
        op.f("ix_server_account_ignored_login_department_id"),
        "server_account_ignored_login",
        ["department_id"],
        unique=False,
    )

    # NOT EXISTS защищает от unique-violation на partial global index при
    # re-apply (тот же стиль, что у `831ba55543e9` / `a1c5e9f3b7d2`).
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
        "WHERE entity_type = 'server_account' AND action = 'manage_ignored_logins'"
    )
    op.drop_index(
        op.f("ix_server_account_ignored_login_department_id"),
        table_name="server_account_ignored_login",
    )
    op.drop_table("server_account_ignored_login")
