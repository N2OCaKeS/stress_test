"""probe settings singleton

Revision ID: a1f5c2d9e7b3
Revises: c7d1e4b9a6f2
Create Date: 2026-07-09 00:00:00.000000

Платформенный singleton настроек проб статуса: частота и вкл/выкл двух групп
проб (reachability = ping+ssh, power = ipmi/domstate), которые снимает
server_worker. Настройки в БД (а не в env воркера), чтобы работать одинаково в
docker и k8s. Одна строка на всю платформу, PK фиксирован значением `default`.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a1f5c2d9e7b3"
down_revision: Union[str, None] = "c7d1e4b9a6f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "probe_settings",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column(
            "reachability_probe_interval_seconds",
            sa.Integer(),
            nullable=False,
            server_default="60",
        ),
        sa.Column(
            "power_probe_interval_seconds",
            sa.Integer(),
            nullable=False,
            server_default="300",
        ),
        sa.Column(
            "reachability_probe_enabled",
            sa.Boolean(),
            nullable=False,
            server_default="true",
        ),
        sa.Column(
            "power_probe_enabled",
            sa.Boolean(),
            nullable=False,
            server_default="true",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("probe_settings")
