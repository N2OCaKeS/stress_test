"""stp composition scope

Revision ID: b2f6a913c7d4
Revises: e2c7a94f1b83
Create Date: 2026-09-15 12:00:00.000000

§D4/D5 плана миграции: явный режим состава СТП (`changelog`/`full`) вместо
угадывания первого/последнего РЦ по последней цифре версии, плюс явная
ревизия состава.

* `stp_cells.is_active` — новая колонка, `server_default=true`. Входит ли
  ячейка в текущий активный состав; переключение changelog/full деактивирует
  ячейки вне объёма, не удаляя их (статус/история остаются).
* `stp_compositions` — новая таблица, одна строка на `(department_id,
  os_version_id)` (`UNIQUE`), несёт текущий `scope` (CHECK `changelog`/
  `full`) и `revision` (растёт только при реальной смене `scope`).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b2f6a913c7d4"
down_revision: Union[str, None] = "e2c7a94f1b83"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "stp_cells",
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
    )

    op.create_table(
        "stp_compositions",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("department_id", sa.String(length=64), nullable=False),
        sa.Column("os_version_id", sa.String(length=64), nullable=False),
        sa.Column("scope", sa.String(length=16), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("updated_by", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.CheckConstraint("scope IN ('changelog', 'full')", name="ck_stp_compositions_scope"),
    )
    op.create_index("ix_stp_compositions_department_id", "stp_compositions", ["department_id"])
    op.create_index("ix_stp_compositions_os_version_id", "stp_compositions", ["os_version_id"])
    op.create_unique_constraint(
        "uq_stp_compositions_dept_os_version", "stp_compositions", ["department_id", "os_version_id"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_stp_compositions_dept_os_version", "stp_compositions", type_="unique")
    op.drop_index("ix_stp_compositions_os_version_id", table_name="stp_compositions")
    op.drop_index("ix_stp_compositions_department_id", table_name="stp_compositions")
    op.drop_table("stp_compositions")

    op.drop_column("stp_cells", "is_active")
