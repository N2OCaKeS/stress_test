"""vm_disks + vm_images tables

Диски и образы VM-менеджера: таблица дисков ВМ (`vm_disks`, каскад с `vms`) и каталог
боксов-образов (`vm_images`, зеркало FTP `test-box-config.json`).

Права зоны vm (`vm_disk_manage` и др.) уже засеяны миграцией b3f7d1a2c8e4 —
новых грантов тут нет. `vm.update` использует уже существующее действие
`update`, диски — `vm_disk_manage`, каталог образов — `vm_preset_manage`.

Downgrade: дроп обеих таблиц.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY

revision: str = "c8a2f4d1b9e7"
down_revision: Union[str, None] = "b3f7d1a2c8e4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── vm_disks ─────────────────────────────────────────────────────────────
    op.create_table(
        "vm_disks",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("vm_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("size_gb", sa.Integer(), nullable=False),
        sa.Column("path", sa.String(length=512), nullable=True),
        sa.Column("target_dev", sa.String(length=16), nullable=True),
        sa.Column("serial", sa.String(length=128), nullable=True),
        sa.Column("is_system", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("fs", sa.String(length=32), nullable=True),
        sa.Column("mount", sa.String(length=255), nullable=True),
        sa.Column("state", sa.String(length=16), nullable=False, server_default="creating"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.ForeignKeyConstraint(["vm_id"], ["vms.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("vm_id", "name", name="uq_vm_disk_name"),
    )
    op.create_index("ix_vm_disks_vm_id", "vm_disks", ["vm_id"])

    # ── vm_images ────────────────────────────────────────────────────────────
    op.create_table(
        "vm_images",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("url", sa.String(length=2048), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False, server_default="single"),
        sa.Column("hub_server_id", sa.String(length=64), nullable=True),
        sa.Column(
            "os_versions", ARRAY(sa.String()),
            nullable=False, server_default="{}",
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.ForeignKeyConstraint(["hub_server_id"], ["servers.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("name", "hub_server_id", name="uq_vm_image_name_hub"),
    )
    op.create_index("ix_vm_images_hub_server_id", "vm_images", ["hub_server_id"])
    # Глобальные образы (hub_server_id IS NULL) — уникальны по имени.
    op.create_index(
        "uq_vm_image_global_name", "vm_images", ["name"],
        unique=True, postgresql_where=sa.text("hub_server_id IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_vm_image_global_name", table_name="vm_images")
    op.drop_index("ix_vm_images_hub_server_id", table_name="vm_images")
    op.drop_table("vm_images")
    op.drop_index("ix_vm_disks_vm_id", table_name="vm_disks")
    op.drop_table("vm_disks")
