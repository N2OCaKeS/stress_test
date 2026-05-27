"""server prepared fields + worker_bot prepare_callback grant

Revision ID: d1f4a8c7b3e9
Revises: c4f7d9b2a1e8
Create Date: 2026-05-27 21:10:00.000000

Бутстрап управления сервером (#14):

  * `servers.is_managed` (bool, default false) — прошёл ли сервер prepare;
  * `servers.management_user` (varchar 64, nullable) — имя управляющего
    пользователя DBOS, заведённого воркером;
  * `servers.prepared_at` (timestamptz, nullable) — момент подтверждения
    онбординга воркером.

Плюс один узкий least-privilege грант для роли `worker_bot`, чтобы воркер мог
подтверждать завершение бутстрапа через callback:

  * `(server, prepare_callback)` — POST /internal/servers/{id}/prepared.

Грант callback-only, без CRUD над сервером. Триггер `prepare` у пользователя
гейтится существующим `update` на `server` — отдельного user-action не заводим.

Downgrade: дроп трёх колонок + удаление гранта.
"""
from typing import Sequence, Union
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision: str = "d1f4a8c7b3e9"
down_revision: Union[str, None] = "c4f7d9b2a1e8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_GRANT = ("server", "prepare_callback")


def upgrade() -> None:
    op.add_column(
        "servers",
        sa.Column(
            "is_managed", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    op.add_column("servers", sa.Column("management_user", sa.String(length=64), nullable=True))
    op.add_column("servers", sa.Column("prepared_at", sa.DateTime(timezone=True), nullable=True))
    # server_default нужен только чтобы заполнить существующие строки; снимаем
    # его, дальше значение приходит из ORM (default=False).
    op.alter_column("servers", "is_managed", server_default=None)

    table = sa.table(
        "entity_permissions",
        sa.column("id", sa.String),
        sa.column("entity_type", sa.String),
        sa.column("role", sa.String),
        sa.column("action", sa.String),
    )
    entity_type, action = _GRANT
    op.bulk_insert(
        table,
        [
            {
                "id": f"prm_{uuid4().hex}",
                "entity_type": entity_type,
                "role": "worker_bot",
                "action": action,
            }
        ],
    )


def downgrade() -> None:
    entity_type, action = _GRANT
    op.execute(
        "DELETE FROM entity_permissions "
        "WHERE role = 'worker_bot' "
        f"  AND entity_type = '{entity_type}' "
        f"  AND action = '{action}'"
    )
    op.drop_column("servers", "prepared_at")
    op.drop_column("servers", "management_user")
    op.drop_column("servers", "is_managed")
