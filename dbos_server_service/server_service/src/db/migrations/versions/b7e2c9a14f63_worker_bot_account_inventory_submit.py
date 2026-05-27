"""worker_bot grant for OS-user inventory callback

Revision ID: b7e2c9a14f63
Revises: a3f1b8c2d495
Create Date: 2026-05-27 18:20:00.000000

Один узкий least-privilege грант для роли `worker_bot`, чтобы воркер мог
постить результат инвентаризации OS-пользователей обратно:

  * `(server_account, inventory_submit)` — POST /internal/servers/{id}/users/inventory.

Этого достаточно для reconcile-callback'а и не даёт воркеру ни CRUD над
аккаунтами, ни прав на чтение паролей сверх уже выданных. `inventory_trigger`
НЕ добавляется — он для пользователя, который *запускает* инвентаризацию, а не
для воркера, который только сдаёт результат.

Downgrade: удаляет ровно эту одну строку.
"""
from typing import Sequence, Union
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision: str = "b7e2c9a14f63"
down_revision: Union[str, None] = "a3f1b8c2d495"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_GRANT = ("server_account", "inventory_submit")


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
