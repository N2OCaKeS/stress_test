"""server_account source + per-server inventory fields

Revision ID: a3f1b8c2d495
Revises: d7e1a4c93b62
Create Date: 2026-05-27 18:10:00.000000

Под инвентаризацию OS-пользователей:

1. `server_accounts.source` — `managed` (заведён через API, пароль известен)
   либо `discovered` (найден инвентаризацией, пароля у API нет). Существующие
   строки = `managed`.
2. На связке `server_account_servers`:
   * `last_inventory_at` — когда аккаунт последний раз видели на этом сервере;
   * `present_on_server` — присутствует ли (False = drift, найден в API, но не
     на боксе). Инвентаризация per-server, поэтому поля на связке, не на
     аккаунте.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a3f1b8c2d495"
down_revision: Union[str, None] = "d7e1a4c93b62"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "server_accounts",
        sa.Column(
            "source",
            sa.String(length=16),
            nullable=False,
            server_default="managed",
        ),
    )
    op.add_column(
        "server_account_servers",
        sa.Column("last_inventory_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "server_account_servers",
        sa.Column(
            "present_on_server",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )
    # server_default нужен только для backfill существующих строк; новые
    # строки получают значение из ORM-дефолта.
    op.alter_column("server_accounts", "source", server_default=None)
    op.alter_column("server_account_servers", "present_on_server", server_default=None)


def downgrade() -> None:
    op.drop_column("server_account_servers", "present_on_server")
    op.drop_column("server_account_servers", "last_inventory_at")
    op.drop_column("server_accounts", "source")
