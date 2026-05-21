"""seed worker_bot least-privilege entity permissions

Revision ID: 43cf9cfef9e1
Revises: 831ba55543e9
Create Date: 2026-05-20 12:44:15.000000

Adds minimal-scope grants for the `worker_bot` role used by `server_worker`:

  worker_bot — only secret-access actions on `server_account` and
               `ipmi_controller`. Cannot manage servers, roles, permissions
               or power state — those operations stay with regular users.

The role definition itself lives in `auth_service` (per-(department, service)).
This migration only adds the `entity_permissions` rows so that, once a user/bot
is assigned `worker_bot` in `server_service`, the existing permission matrix
authorises the four credential actions and denies everything else.

Сужает права worker_bot-токена до минимально необходимых: раньше PAT воркера
имел глобальный admin, что нарушало least-privilege.

Companion changes:
- `scripts/seed_dev.py` — creates the `worker_bot` role-definition for НТ
  department and assigns it to `server_worker_user` instead of `admin`.
- `tests/unit/test_worker_bot_grants.py` — verifies grants are exactly the
  four declared below and that worker_bot is **not** present for forbidden
  actions (`server.delete`, `permission.grant`, `power_on`, etc.).

Downgrade: deletes all `worker_bot` rows.
"""
from typing import Sequence, Union
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision: str = "43cf9cfef9e1"
down_revision: Union[str, None] = "831ba55543e9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# (entity_type, action) — explicit allow-list for the worker_bot role.
# Anything missing here is denied by default through the permission matrix.
WORKER_BOT_GRANTS: list[tuple[str, str]] = [
    # server_worker reads stored credentials to drive remote IPMI/account ops.
    ("server_account", "view_password"),
    # server_worker writes back the new ciphertext after a password rotation.
    ("server_account", "rotate_password"),
    # server_worker decrypts IPMI credentials before talking to iDRAC/iLO/etc.
    ("ipmi_controller", "view_credentials"),
    # server_worker submits the new IPMI password ciphertext after rotation.
    # Kept here so the worker can later own the ipmi.rotate_credentials task
    # without re-issuing the PAT (see follow-up «ipmi.rotate_credentials
    # endpoint + worker submit-step»).
    ("ipmi_controller", "rotate_credentials"),
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
            for entity_type, action in WORKER_BOT_GRANTS
        ],
    )


def downgrade() -> None:
    op.execute("DELETE FROM entity_permissions WHERE role = 'worker_bot'")
