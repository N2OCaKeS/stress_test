"""vm_snapshots table (snapshots + per-snapshot mgmt creds)

Волна 3 VM-менеджера: таблица снимков ВМ (`vm_snapshots`, каскад с `vms`,
self-FK на цепочку) + шифр mgmt-креды на момент снимка (режим per_snapshot).

Права зоны vm (`vm_snapshot_manage`, `vm_astra_update`, `vm_allta_update`,
`vm_passwd`) уже засеяны миграцией b3f7d1a2c8e4 роли admin — новых грантов тут
нет. Callback `POST /internal/vms/{id}/snapshots` авторизуется уже засеянным
`(server, prepare_callback)`.

Downgrade: дроп таблицы.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d3b9f1a7c284"
down_revision: Union[str, None] = "c8a2f4d1b9e7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "vm_snapshots",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("vm_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.String(length=1024), nullable=True),
        sa.Column("parent_snapshot_id", sa.String(length=64), nullable=True),
        sa.Column("kind", sa.String(length=16), nullable=False, server_default="disk_only"),
        sa.Column("is_system", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("state", sa.String(length=16), nullable=False, server_default="creating"),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("is_current", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("mgmt_user", sa.String(length=64), nullable=True),
        sa.Column("mgmt_password_encrypted", sa.Text(), nullable=True),
        sa.Column("mgmt_ssh_private_key_encrypted", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(["vm_id"], ["vms.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["parent_snapshot_id"], ["vm_snapshots.id"], ondelete="SET NULL",
        ),
        sa.UniqueConstraint("vm_id", "name", name="uq_vm_snapshot_name"),
    )
    op.create_index("ix_vm_snapshots_vm_id", "vm_snapshots", ["vm_id"])
    op.create_index(
        "ix_vm_snapshots_vm_current", "vm_snapshots", ["vm_id", "is_current"],
    )


def downgrade() -> None:
    op.drop_index("ix_vm_snapshots_vm_current", table_name="vm_snapshots")
    op.drop_index("ix_vm_snapshots_vm_id", table_name="vm_snapshots")
    op.drop_table("vm_snapshots")
