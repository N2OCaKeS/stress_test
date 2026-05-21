"""add bmc_vendor column to ipmi_controllers

Revision ID: a4b7e1c92d05
Revises: f1234abc56e7
Create Date: 2026-05-22 06:00:00.000000

`ipmi_controllers.kind` фиксирует протокол/транспорт (idrac/ilo/ipmi/redfish),
но не различает vendor BMC для построения Redfish-paths. iDRAC использует
`Managers/iDRAC.Embedded.1`, HP iLO — `Managers/1`, остальные BMC требуют
discovery через коллекцию `/Managers`.

Колонка `bmc_vendor` (idrac / ilo / ipmi_generic) даёт worker'у явный
сигнал для выбора manager-path без heuristics на сторону kind. NOT NULL
с server_default `ipmi_generic` — существующие записи получают безопасный
fallback (worker discover'ит manager-id через коллекцию).

После наката `server_default` на уровне DB остаётся — это страхует ORM-INSERT'ы,
которые не передадут поле явно. На уровне SQLAlchemy-модели также прописан
`server_default="ipmi_generic"`.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a4b7e1c92d05"
down_revision: Union[str, None] = "f1234abc56e7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "ipmi_controllers",
        sa.Column(
            "bmc_vendor",
            sa.String(length=32),
            nullable=False,
            server_default="ipmi_generic",
        ),
    )


def downgrade() -> None:
    op.drop_column("ipmi_controllers", "bmc_vendor")
