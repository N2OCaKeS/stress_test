"""drop bmc_vendor column from ipmi_controllers

Revision ID: d5e8a1c3f960
Revises: c1a9f2b7e4d8
Create Date: 2026-05-27 12:50:00.000000

Поле `bmc_vendor` дублировало `kind` (idrac / ilo / ipmi / redfish) и было
единственным потребителем worker — для выбора Redfish manager-path. Выбор
переключён на `kind`, колонка больше не нужна.

downgrade воссоздаёт колонку с прежним server_default `ipmi_generic`, чтобы
откат не падал на NOT NULL для существующих строк.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d5e8a1c3f960"
down_revision: Union[str, None] = "c1a9f2b7e4d8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("ipmi_controllers", "bmc_vendor")


def downgrade() -> None:
    op.add_column(
        "ipmi_controllers",
        sa.Column(
            "bmc_vendor",
            sa.String(length=32),
            nullable=False,
            server_default="ipmi_generic",
        ),
    )
