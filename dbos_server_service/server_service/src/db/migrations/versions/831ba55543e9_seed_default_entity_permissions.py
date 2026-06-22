"""seed default entity permissions (action-based)

Revision ID: 831ba55543e9
Revises: d3ad4aac49cc
Create Date: 2026-05-14 14:33:27.382402

Per-role action grants:

  guest    — no actions (placeholder role for completeness)
  reader   — `view` only on all entity types; sensitive secrets stay closed
  operator — operational CRUD on operational entities; view-only on catalogues;
             can rotate but never reveal passwords/credentials in plaintext;
             cannot grant sudo and cannot manage roles/permissions
  admin    — every action listed in `_ALL_ACTIONS` (kept in sync with constants.py)

Sensitive actions (`view_password`, `view_credentials`, `grant_sudo`) are
operator-OFF by default — granting them is an explicit admin action via the
/permissions endpoints.
"""
from typing import Sequence, Union
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision: str = "831ba55543e9"
down_revision: Union[str, None] = "d3ad4aac49cc"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Mirror of constants.ENTITY_ACTIONS — duplicated here on purpose so the
# migration is self-contained and survives changes to constants.py.
_ALL_ACTIONS: dict[str, list[str]] = {
    "server": [
        "view", "create", "update", "delete",
        "busy_acquire", "busy_release",
        "os_sync",
        "power_on", "power_off", "power_reboot", "power_status",
        "boot_order_view", "boot_order_set",
        "pxe_boot", "reinstall_start",
        "inventory_trigger", "inventory_submit",
    ],
    "server_account": [
        "view", "create", "update", "delete",
        "view_password", "rotate_password",
        "grant_sudo",
        "provision", "deprovision",
    ],
    "os_version": ["view", "create", "update", "delete"],
    "ipmi_controller": [
        "view", "create", "update", "delete",
        "view_credentials", "rotate_credentials",
    ],
    "cpu_model": ["view", "create", "update", "delete"],
    "disk": ["view", "create", "update", "delete"],
    "permission": [
        "view", "permission_grant", "permission_revoke",
    ],
}

# Operator gets these actions on operational entity types.
# Anything missing from the list is denied for `operator` by default.
_OPERATOR_GRANTS: dict[str, list[str]] = {
    "server": [
        "view", "create", "update",
        "busy_acquire", "busy_release",
        "os_sync",
        "power_on", "power_off", "power_reboot", "power_status",
        "boot_order_view", "boot_order_set",
        "pxe_boot", "reinstall_start",
        "inventory_trigger", "inventory_submit",
    ],
    "server_account": ["view", "create", "update", "rotate_password", "provision"],
    "ipmi_controller": ["view", "create", "update", "rotate_credentials"],
    "disk": ["view", "create", "update"],
    "os_version": ["view"],
    "cpu_model": ["view"],
    "permission": ["view"],
}


def _grants() -> list[tuple[str, str, str]]:
    rows: list[tuple[str, str, str]] = []

    # admin — everything
    for entity_type, actions in _ALL_ACTIONS.items():
        for action in actions:
            rows.append((entity_type, "admin", action))

    # reader — view on every entity type that supports `view`
    for entity_type, actions in _ALL_ACTIONS.items():
        if "view" in actions:
            rows.append((entity_type, "reader", "view"))

    # operator — explicit per-entity grants
    for entity_type, actions in _OPERATOR_GRANTS.items():
        for action in actions:
            rows.append((entity_type, "operator", action))

    return rows


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
                "role": role,
                "action": action,
            }
            for entity_type, role, action in _grants()
        ],
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM entity_permissions WHERE role IN ('reader', 'operator', 'admin')"
    )
