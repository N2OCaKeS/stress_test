"""server_account to servers many-to-many

Revision ID: d7e1a4c93b62
Revises: c1a9f2b7e4d8
Create Date: 2026-05-27 16:40:00.000000

Аккаунт может жить сразу на нескольких серверах.

Схема:

1. Новая join-таблица `server_account_servers(account_id, server_id, login)`
   с двумя UNIQUE:
   * `uq_account_server` — `(account_id, server_id)` — дубль-связку нельзя;
   * `uq_server_login` — `(server_id, login)` — один логин на сервер
     (инвариант, который раньше держал UNIQUE на самой строке аккаунта).
2. `server_accounts` получает `department_id` (NOT NULL) — владелец аккаунта;
   все привязанные серверы обязаны быть в этом отделе.
3. Старый одиночный `server_accounts.server_id` (FK CASCADE + индекс +
   UNIQUE(server_id, login)) убирается.

Перенос данных:

* для каждой существующей строки `server_accounts` создаётся ровно одна
  связка из её `server_id` + `login`;
* `department_id` берётся из `servers.department_id` по этому `server_id`.

Порядок в upgrade: добавить колонку (nullable) → создать join → залить
связки → заполнить department_id → сделать колонку NOT NULL → снять старый
server_id.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d7e1a4c93b62"
down_revision: Union[str, None] = "d5e8a1c3f960"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. join-таблица.
    op.create_table(
        "server_account_servers",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("account_id", sa.String(length=64), nullable=False),
        sa.Column("server_id", sa.String(length=64), nullable=False),
        sa.Column("login", sa.String(length=128), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["account_id"], ["server_accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["server_id"], ["servers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("account_id", "server_id", name="uq_account_server"),
        sa.UniqueConstraint("server_id", "login", name="uq_server_login"),
    )
    op.create_index(
        op.f("ix_server_account_servers_account_id"),
        "server_account_servers", ["account_id"], unique=False,
    )
    op.create_index(
        op.f("ix_server_account_servers_server_id"),
        "server_account_servers", ["server_id"], unique=False,
    )

    # 2. department_id на аккаунте (пока nullable — заполним из servers).
    op.add_column(
        "server_accounts",
        sa.Column("department_id", sa.String(length=64), nullable=True),
    )

    # 3. перенос связок: одна строка на каждый существующий аккаунт.
    op.execute(
        """
        INSERT INTO server_account_servers (id, account_id, server_id, login, created_at)
        SELECT 'acs_' || replace(gen_random_uuid()::text, '-', ''),
               sa.id, sa.server_id, sa.login, now()
        FROM server_accounts sa
        """
    )

    # 4. заполнить department_id из сервера, к которому аккаунт был привязан.
    op.execute(
        """
        UPDATE server_accounts sa
        SET department_id = s.department_id
        FROM servers s
        WHERE s.id = sa.server_id
        """
    )

    # 5. department_id обязателен + индекс.
    op.alter_column("server_accounts", "department_id", nullable=False)
    op.create_index(
        op.f("ix_server_accounts_department_id"),
        "server_accounts", ["department_id"], unique=False,
    )

    # 6. снять старый одиночный server_id.
    op.drop_constraint("uq_server_account_login", "server_accounts", type_="unique")
    op.drop_index(op.f("ix_server_accounts_server_id"), table_name="server_accounts")
    op.drop_constraint(
        "server_accounts_server_id_fkey", "server_accounts", type_="foreignkey",
    )
    op.drop_column("server_accounts", "server_id")


def downgrade() -> None:
    # Вернуть одиночный server_id. Берём первую связку каждого аккаунта.
    op.add_column(
        "server_accounts",
        sa.Column("server_id", sa.String(length=64), nullable=True),
    )
    op.execute(
        """
        UPDATE server_accounts sa
        SET server_id = link.server_id
        FROM (
            SELECT DISTINCT ON (account_id) account_id, server_id
            FROM server_account_servers
            ORDER BY account_id, created_at
        ) link
        WHERE link.account_id = sa.id
        """
    )
    op.alter_column("server_accounts", "server_id", nullable=False)
    op.create_foreign_key(
        "server_accounts_server_id_fkey",
        "server_accounts", "servers",
        ["server_id"], ["id"], ondelete="CASCADE",
    )
    op.create_index(
        op.f("ix_server_accounts_server_id"),
        "server_accounts", ["server_id"], unique=False,
    )
    op.create_unique_constraint(
        "uq_server_account_login", "server_accounts", ["server_id", "login"],
    )

    op.drop_index(op.f("ix_server_accounts_department_id"), table_name="server_accounts")
    op.drop_column("server_accounts", "department_id")

    op.drop_index(
        op.f("ix_server_account_servers_server_id"), table_name="server_account_servers",
    )
    op.drop_index(
        op.f("ix_server_account_servers_account_id"), table_name="server_account_servers",
    )
    op.drop_table("server_account_servers")
