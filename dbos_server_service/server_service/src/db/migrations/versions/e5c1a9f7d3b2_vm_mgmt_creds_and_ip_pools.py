"""vm per-VM mgmt creds columns + vm_ip_pool table (IPAM)

Управляющие креды ВМ и IPAM:
* per-VM управляющие креды на `vms` (зеркало серверных): `is_managed`,
  `mgmt_user`, `mgmt_ssh_public_key`, шифр `mgmt_ssh_private_key_encrypted`/
  `mgmt_password_encrypted`, `mgmt_creds_pending_apply`, `mgmt_creds_rotated_at`.
* таблица `vm_ip_pool` — пулы IP-адресов bridge-ВМ (IPAM).

Права зоны vm (`vm_prepare`, `vm_net_manage`) уже засеяны миграцией
b3f7d1a2c8e4 роли admin — новых грантов тут нет. Internal-fetch mgmt-кред ВМ
авторизуется уже засеянным `(server, view_management_credentials)`, callback
`POST /internal/vms/{id}/prepared` — `(server, prepare_callback)`.

Downgrade: дроп таблицы vm_ip_pool + дроп mgmt-колонок vms.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import INET, JSONB

revision: str = "e5c1a9f7d3b2"
down_revision: Union[str, None] = "d3b9f1a7c284"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── vms: per-VM управляющие креды ────────────────────────────────────────
    op.add_column(
        "vms",
        sa.Column("is_managed", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("vms", sa.Column("mgmt_user", sa.String(length=64), nullable=True))
    op.add_column("vms", sa.Column("mgmt_ssh_public_key", sa.Text(), nullable=True))
    op.add_column(
        "vms", sa.Column("mgmt_ssh_private_key_encrypted", sa.Text(), nullable=True)
    )
    op.add_column(
        "vms", sa.Column("mgmt_password_encrypted", sa.Text(), nullable=True)
    )
    op.add_column(
        "vms",
        sa.Column(
            "mgmt_creds_pending_apply", sa.Boolean(),
            nullable=False, server_default=sa.false(),
        ),
    )
    op.add_column(
        "vms",
        sa.Column("mgmt_creds_rotated_at", sa.DateTime(timezone=True), nullable=True),
    )

    # ── vm_ip_pool ───────────────────────────────────────────────────────────
    op.create_table(
        "vm_ip_pool",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("department_id", sa.String(length=64), nullable=False),
        sa.Column("cidr", sa.String(length=64), nullable=False),
        sa.Column("gateway", INET(), nullable=True),
        sa.Column("netmask", sa.String(length=64), nullable=True),
        sa.Column("dns", JSONB(), nullable=True),
        sa.Column("range_start", INET(), nullable=False),
        sa.Column("range_end", INET(), nullable=False),
        sa.Column("server_id", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.Column("created_by", sa.String(length=64), nullable=True),
    )
    op.create_index("ix_vm_ip_pool_department_id", "vm_ip_pool", ["department_id"])
    op.create_index("ix_vm_ip_pool_server_id", "vm_ip_pool", ["server_id"])
    op.create_index(
        "uq_vm_ip_pool_dept_name", "vm_ip_pool", ["department_id", "name"], unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_vm_ip_pool_dept_name", table_name="vm_ip_pool")
    op.drop_index("ix_vm_ip_pool_server_id", table_name="vm_ip_pool")
    op.drop_index("ix_vm_ip_pool_department_id", table_name="vm_ip_pool")
    op.drop_table("vm_ip_pool")
    op.drop_column("vms", "mgmt_creds_rotated_at")
    op.drop_column("vms", "mgmt_creds_pending_apply")
    op.drop_column("vms", "mgmt_password_encrypted")
    op.drop_column("vms", "mgmt_ssh_private_key_encrypted")
    op.drop_column("vms", "mgmt_ssh_public_key")
    op.drop_column("vms", "mgmt_user")
    op.drop_column("vms", "is_managed")
