"""vm batch: account↔vm join, hostname, image min_disk, snapshot mode/kind

Батч-создание ВМ + учётки + hostname + снимки по режимам + мин-размер диска.

- `server_account_vms` — зеркальный join учётка↔ВМ (тот же server_account для
  серверов и ВМ, без новой сущности).
- `vms.hostname` — hostname гостя (пусто → имя ВМ).
- `vm_images.min_disk_gb` — минимальный размер системного диска бокса (для
  предупреждения в UI до создания).
- `vm_snapshots`: старый `kind` (disk_only/full) → `snapshot_type`; новый `kind`
  (os_baseline/user) + `os_version` + `mode` (oryol/smolensk).

Downgrade — обратные операции.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b2f9a7c31d84"
down_revision: Union[str, None] = "f4d1b9e3c7a2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "server_account_vms",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column(
            "account_id", sa.String(length=64),
            sa.ForeignKey("server_accounts.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "vm_id", sa.String(length=64),
            sa.ForeignKey("vms.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("login", sa.String(length=128), nullable=False),
        sa.Column(
            "present_on_vm", sa.Boolean(),
            nullable=False, server_default=sa.true(),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.UniqueConstraint("account_id", "vm_id", name="uq_account_vm"),
        sa.UniqueConstraint("vm_id", "login", name="uq_vm_login"),
    )
    op.create_index(
        "ix_server_account_vms_account_id", "server_account_vms", ["account_id"],
    )
    op.create_index(
        "ix_server_account_vms_vm_id", "server_account_vms", ["vm_id"],
    )

    op.add_column("vms", sa.Column("hostname", sa.String(length=255), nullable=True))
    op.add_column(
        "vm_images", sa.Column("min_disk_gb", sa.Integer(), nullable=True),
    )

    op.alter_column("vm_snapshots", "kind", new_column_name="snapshot_type")
    op.add_column(
        "vm_snapshots",
        sa.Column(
            "kind", sa.String(length=16), nullable=False, server_default="user",
        ),
    )
    op.add_column(
        "vm_snapshots", sa.Column("os_version", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "vm_snapshots", sa.Column("mode", sa.String(length=16), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("vm_snapshots", "mode")
    op.drop_column("vm_snapshots", "os_version")
    op.drop_column("vm_snapshots", "kind")
    op.alter_column("vm_snapshots", "snapshot_type", new_column_name="kind")

    op.drop_column("vm_images", "min_disk_gb")
    op.drop_column("vms", "hostname")

    op.drop_index("ix_server_account_vms_vm_id", table_name="server_account_vms")
    op.drop_index("ix_server_account_vms_account_id", table_name="server_account_vms")
    op.drop_table("server_account_vms")
