"""Virtual test stands — test_stands.target_type + vm_id

Revision ID: e8c4b1f7a3d5
Revises: a3c8e5f20d71
Create Date: 2026-09-24 00:00:00.000000

стенд — физический сервер (`target_type=server`,
`server_id`, как было) или ВМ server_service (`target_type=vm`, `vm_id`).
Ровно одно из `server_id`/`vm_id`. Существующие стенды — `server`, легаси
(`emm/allta_app_full/allta_image_conf.py:364-371` — ВМ-стенды stand1/2/6-9
жили там отдельным словарём `test_station_vms`) заводятся заново руками:
id ВМ в server_service миграции неизвестны.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e8c4b1f7a3d5"
down_revision: Union[str, None] = "a3c8e5f20d71"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "test_stands",
        sa.Column("target_type", sa.String(length=8), nullable=False, server_default="server"),
    )
    op.add_column("test_stands", sa.Column("vm_id", sa.String(length=64), nullable=True))
    op.create_index("ix_test_stands_vm_id", "test_stands", ["vm_id"], unique=True)
    op.alter_column("test_stands", "server_id", existing_type=sa.String(length=64), nullable=True)
    op.create_check_constraint(
        "ck_test_stands_target", "test_stands",
        "(target_type = 'server' AND server_id IS NOT NULL AND vm_id IS NULL) OR "
        "(target_type = 'vm' AND vm_id IS NOT NULL AND server_id IS NULL)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_test_stands_target", "test_stands", type_="check")
    # ВМ-стенды без server_id в старой схеме не выразить — снимаем их.
    op.execute("DELETE FROM test_stands WHERE target_type = 'vm'")
    op.alter_column("test_stands", "server_id", existing_type=sa.String(length=64), nullable=False)
    op.drop_index("ix_test_stands_vm_id", table_name="test_stands")
    op.drop_column("test_stands", "vm_id")
    op.drop_column("test_stands", "target_type")
