"""vm domain (vms table) + server number/virtualization/vms-hub columns

Базовый слой VM-менеджера: таблица `vms` (карточка ВМ на hub-сервере), новые
колонки серверов (`number`, `virtualization`, `is_vms_hub`,
`vms_hub_prepared_at`) и seed прав зоны `vm` (admin — всё, guest — view).

worker_bot для VM-callback'ов отдельного гранта не получает: авторизация
`POST /internal/vms/{id}/state` и `/internal/servers/{id}/vms-hub-state` идёт
по уже засеянному `(server, prepare_callback)`.

Downgrade: дроп seed'а зоны vm, дроп серверных колонок, дроп таблицы vms.
"""
from typing import Sequence, Union
from uuid import uuid4

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import INET

revision: str = "b3f7d1a2c8e4"
down_revision: Union[str, None] = "f3a7c1e9b2d5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Матрица прав зоны vm (зеркало constants.ENTITY_ACTIONS[VM]) — дублируем тут,
# чтобы миграция была самодостаточной и пережила правки constants.py.
_VM_ACTIONS: list[str] = [
    "view", "create", "update", "delete",
    "vms_hub_prepare",
    "vm_power",
    "vm_reserve", "vm_release",
    "vm_disk_manage", "vm_snapshot_manage",
    "vm_prepare",
    "vm_astra_update", "vm_allta_update", "vm_passwd",
    "vm_net_manage", "vm_preset_manage",
]


def upgrade() -> None:
    # ── servers: number + виртуализация ──────────────────────────────────────
    op.add_column("servers", sa.Column("number", sa.Integer(), nullable=True))
    op.create_unique_constraint("uq_servers_number", "servers", ["number"])
    op.add_column("servers", sa.Column("virtualization", sa.Boolean(), nullable=True))
    op.add_column(
        "servers",
        sa.Column(
            "is_vms_hub", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    op.add_column(
        "servers",
        sa.Column("vms_hub_prepared_at", sa.DateTime(timezone=True), nullable=True),
    )

    # ── vms ──────────────────────────────────────────────────────────────────
    op.create_table(
        "vms",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("number", sa.Integer(), nullable=True),
        sa.Column("hub_server_id", sa.String(length=64), nullable=False),
        sa.Column("department_id", sa.String(length=64), nullable=False),
        sa.Column("os_version", sa.String(length=64), nullable=True),
        sa.Column("box", sa.String(length=128), nullable=True),
        sa.Column(
            "network_mode", sa.String(length=16),
            nullable=False, server_default="bridge",
        ),
        sa.Column("ip_address", INET(), nullable=True),
        sa.Column("status", sa.String(length=64), nullable=False, server_default="free"),
        sa.Column(
            "power_state", sa.String(length=16),
            nullable=False, server_default="unknown",
        ),
        sa.Column("power_state_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cpu", sa.Integer(), nullable=True),
        sa.Column("ram_mb", sa.Integer(), nullable=True),
        sa.Column("disk_gb", sa.Integer(), nullable=True),
        sa.Column("autostart", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "cred_strategy", sa.String(length=16),
            nullable=False, server_default="per_snapshot",
        ),
        sa.Column("busy_state", sa.String(length=16), nullable=True),
        sa.Column("busy_since", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ping_reachable", sa.Boolean(), nullable=True),
        sa.Column("ping_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ssh_reachable", sa.Boolean(), nullable=True),
        sa.Column("ssh_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(length=1024), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(
            ["hub_server_id"], ["servers.id"], ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("number", name="uq_vms_number"),
    )
    op.create_index("ix_vms_hub_server_id", "vms", ["hub_server_id"])
    op.create_index("ix_vms_department_id", "vms", ["department_id"])
    op.create_index(
        "uq_vms_hub_name", "vms", ["hub_server_id", "name"], unique=True,
    )
    op.create_index("ix_vms_department_status", "vms", ["department_id", "status"])

    # ── seed прав зоны vm: admin — всё, guest — view (system-wide) ────────────
    table = sa.table(
        "entity_permissions",
        sa.column("id", sa.String),
        sa.column("entity_type", sa.String),
        sa.column("role", sa.String),
        sa.column("action", sa.String),
    )
    rows = [
        {"id": f"prm_{uuid4().hex}", "entity_type": "vm", "role": "admin", "action": action}
        for action in _VM_ACTIONS
    ]
    rows.append(
        {"id": f"prm_{uuid4().hex}", "entity_type": "vm", "role": "guest", "action": "view"}
    )
    op.bulk_insert(table, rows)


def downgrade() -> None:
    op.execute("DELETE FROM entity_permissions WHERE entity_type = 'vm'")
    op.drop_index("ix_vms_department_status", table_name="vms")
    op.drop_index("uq_vms_hub_name", table_name="vms")
    op.drop_index("ix_vms_department_id", table_name="vms")
    op.drop_index("ix_vms_hub_server_id", table_name="vms")
    op.drop_table("vms")
    op.drop_column("servers", "vms_hub_prepared_at")
    op.drop_column("servers", "is_vms_hub")
    op.drop_column("servers", "virtualization")
    op.drop_constraint("uq_servers_number", "servers", type_="unique")
    op.drop_column("servers", "number")
