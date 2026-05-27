"""worker_bot grant for OS-user provision callback

Revision ID: c4f7d9b2a1e8
Revises: b7e2c9a14f63
Create Date: 2026-05-27 19:40:00.000000

Один узкий least-privilege грант для роли `worker_bot`, чтобы воркер мог
подтверждать результат useradd/usermod/userdel на боксе:

  * `(server_account, provision_on_host)` — POST
    /internal/servers/{id}/accounts/{aid}/provision_status.

Этого достаточно для статус-callback'а и не даёт воркеру CRUD над аккаунтами.
Триггеры provision/update/deprovision у пользователя гейтятся существующими
`create`/`update`/`delete` на `server_account` — отдельных user-action'ов не
заводим.

Downgrade: удаляет ровно эту одну строку.
"""
from typing import Sequence, Union
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision: str = "c4f7d9b2a1e8"
down_revision: Union[str, None] = "b7e2c9a14f63"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_GRANT = ("server_account", "provision_on_host")


def upgrade() -> None:
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
