"""seed allta_bridge least-privilege entity permissions

Revision ID: ad5f3fcf1352
Revises: 36078ae57d75
Create Date: 2026-09-22 00:00:01.000000

Adds minimal-scope grants for the `allta_bridge` role used by the
`allta_app_service` bot: resolve a server by its stand number and read its
decrypted IPMI/BMC credentials to drive iLO power actions. Nothing else —
no server management, no account/secret access beyond IPMI.

  allta_bridge — (server, view) + (ipmi_controller, view_credentials).

The role definition itself lives in `auth_service` (per-(department,
service)). This migration only adds the `entity_permissions` rows so that,
once a bot is assigned `allta_bridge` in `server_service`, the existing
permission matrix authorises the two actions and denies everything else.

Downgrade: deletes all `allta_bridge` rows.
"""
from typing import Sequence, Union
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision: str = "ad5f3fcf1352"
down_revision: Union[str, None] = "36078ae57d75"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# (entity_type, action) — explicit allow-list for the allta_bridge role.
# Anything missing here is denied by default through the permission matrix.
ALLTA_BRIDGE_GRANTS: list[tuple[str, str]] = [
    # resolve stand number -> server_id via GET /servers/by-stand-number/{n}
    ("server", "view"),
    # decrypt IPMI credentials via GET /internal/servers/{id}/ipmi/credentials
    ("ipmi_controller", "view_credentials"),
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
                "role": "allta_bridge",
                "action": action,
            }
            for entity_type, action in ALLTA_BRIDGE_GRANTS
        ],
    )


def downgrade() -> None:
    op.execute("DELETE FROM entity_permissions WHERE role = 'allta_bridge'")
