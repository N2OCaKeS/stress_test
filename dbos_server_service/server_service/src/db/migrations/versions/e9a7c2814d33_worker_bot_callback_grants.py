"""worker_bot grants for write-direction callback endpoints

Revision ID: e9a7c2814d33
Revises: c7b41a9d2f08
Create Date: 2026-05-21 12:45:00.000000

Two extra least-privilege grants for the `worker_bot` role so it can call the
three internal callback endpoints introduced for server_worker:

  * `(server, inventory_submit)` — POST /internal/servers/{id}/inventory.
  * `(server, reinstall_start)` — POST /internal/servers/{id}/reinstall_status.

`(ipmi_controller, rotate_credentials)` is already seeded by
`43cf9cfef9e1_seed_worker_bot_entity_permissions` and covers the third callback
(`POST /internal/ipmi-controllers/{id}/credentials_rotated`).

`inventory_trigger` is NOT added — that action is for users who *start* the
sync, not for the worker that pushes the result back. Likewise the worker can
report `reinstall_status` (via the same `reinstall_start` action) but cannot
trigger an entirely new reinstall pipeline from scratch through some other path.

Downgrade: deletes the two rows added by this migration only.
"""
from typing import Sequence, Union
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision: str = "e9a7c2814d33"
down_revision: Union[str, None] = "abd8298d5349"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_NEW_WORKER_BOT_GRANTS: list[tuple[str, str]] = [
    ("server", "inventory_submit"),
    ("server", "reinstall_start"),
]


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
                "entity_type": entity_type,
                "role": "worker_bot",
                "action": action,
            }
            for entity_type, action in _NEW_WORKER_BOT_GRANTS
        ],
    )


def downgrade() -> None:
    for entity_type, action in _NEW_WORKER_BOT_GRANTS:
        op.execute(
            "DELETE FROM entity_permissions "
            f"WHERE role = 'worker_bot' "
            f"  AND entity_type = '{entity_type}' "
            f"  AND action = '{action}'"
        )
