"""vm packages inventory + graphics/graphics_port on vms

Бэкенд VM-вкладок «пакеты» и «консоль»:

- `vm_package_inventory` — сохранённый список пакетов гостя (одна строка на ВМ,
  JSONB), который пишет callback `record_vm_packages` и отдаёт
  `GET /vms/{id}/packages`.
- `vms.graphics` — тип графической консоли (vnc/spice), выбирается при создании
  и уезжает воркеру как `--graphics`.
- `vms.graphics_port` — порт дисплея на hub'е, который воркер сообщает в
  state-callback'е; попадает в токен консоли (иначе прокси резолвит через virsh).

Downgrade — обратные операции.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "c7d1e4b9a6f2"
down_revision: Union[str, None] = "b2f9a7c31d84"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "vms",
        sa.Column(
            "graphics", sa.String(length=8), nullable=False, server_default="vnc",
        ),
    )
    op.add_column("vms", sa.Column("graphics_port", sa.Integer(), nullable=True))

    op.create_table(
        "vm_package_inventory",
        sa.Column(
            "vm_id", sa.String(length=64),
            sa.ForeignKey("vms.id", ondelete="CASCADE"), primary_key=True,
        ),
        sa.Column(
            "packages", JSONB(), nullable=False, server_default="[]",
        ),
        sa.Column(
            "package_count", sa.Integer(), nullable=False, server_default="0",
        ),
        sa.Column("source", sa.String(length=16), nullable=True),
        sa.Column("task_id", sa.String(length=64), nullable=True),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("vm_package_inventory")
    op.drop_column("vms", "graphics_port")
    op.drop_column("vms", "graphics")
