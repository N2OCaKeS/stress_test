"""seed worker_bot view_management_credentials grant

Revision ID: d8b3e1c7a5f2
Revises: c9f2a7b4e6d1
Create Date: 2026-06-29

Узкий least-privilege грант для роли worker_bot: воркер тянет per-server
управляющие креды (privkey + пароль dbos) через
`GET /internal/servers/{id}/management/credentials` перед каждой managed-
операцией. Один (entity, action) — `(server, view_management_credentials)`.

Downgrade удаляет только эту строку, не трогая остальные worker_bot-гранты.
"""
from typing import Sequence, Union
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision: str = "d8b3e1c7a5f2"
down_revision: Union[str, None] = "c9f2a7b4e6d1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_GRANT: tuple[str, str] = ("server", "view_management_credentials")


def upgrade() -> None:
    table = sa.table(
        "entity_permissions",
        sa.column("id", sa.String),
        sa.column("entity_type", sa.String),
        sa.column("role", sa.String),
        sa.column("action", sa.String),
    )
    op.bulk_insert(
        table,
        [
            {
                "id": f"prm_{uuid4().hex}",
                "entity_type": _GRANT[0],
                "role": "worker_bot",
                "action": _GRANT[1],
            }
        ],
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM entity_permissions "
        "WHERE role = 'worker_bot' "
        "AND entity_type = 'server' "
        "AND action = 'view_management_credentials'"
    )
