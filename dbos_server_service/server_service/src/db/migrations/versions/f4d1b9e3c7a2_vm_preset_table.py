"""vm_preset table (standard VM templates)

Таблица `vm_preset` — шаблоны стандартных ВМ отдела (NQ6). Разворачиваются на
hub'е через `POST /servers/{id}/create-default-vms` по правилу deploy-once
(bridge — 1 раз глобально, nat — 1 раз на hub-сервер).

Право `vm_preset_manage` и его грант роли admin засеяны раньше (миграция
b3f7d1a2c8e4, зона vm) — новых грантов тут нет.

Downgrade: дроп таблицы vm_preset.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import INET

revision: str = "f4d1b9e3c7a2"
down_revision: Union[str, None] = "e5c1a9f7d3b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "vm_preset",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("department_id", sa.String(length=64), nullable=False),
        sa.Column("box", sa.String(length=128), nullable=True),
        sa.Column("os_version", sa.String(length=64), nullable=True),
        sa.Column("cpu", sa.Integer(), nullable=False),
        sa.Column("ram_mb", sa.Integer(), nullable=False),
        sa.Column("disk_gb", sa.Integer(), nullable=False),
        sa.Column(
            "network_mode", sa.String(length=16),
            nullable=False, server_default="bridge",
        ),
        sa.Column("fixed_ip", INET(), nullable=True),
        sa.Column("number", sa.Integer(), nullable=True),
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
    op.create_index("ix_vm_preset_department_id", "vm_preset", ["department_id"])
    op.create_index(
        "uq_vm_preset_dept_name", "vm_preset", ["department_id", "name"], unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_vm_preset_dept_name", table_name="vm_preset")
    op.drop_index("ix_vm_preset_department_id", table_name="vm_preset")
    op.drop_table("vm_preset")
