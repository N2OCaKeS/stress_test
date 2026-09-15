"""statistics settings and recalc status

Revision ID: b7a4d2c918e5
Revises: 25224c748ae0
Create Date: 2026-09-15 18:00:00.000000

Фоновый пересчёт статистики через внешний сервис (ветка `statistics` этого
же монорепо, §2.7/§9.3 плана миграции): платформенный singleton настроек
(`statistics_settings`, тот же паттерн, что `acs_settings` в server_service)
+ платформенный singleton-индикатор состояния фонового пересчёта
(`statistics_recalc_status`).

Права: `statistics_settings` — системная роль `admin`, действия
`view`/`update` (upsert настроек и ручной триггер пересчёта не заводят
отдельного `create`), тот же паттерн, что `department_test_settings`.
"""
from typing import Sequence, Union
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision: str = "b7a4d2c918e5"
down_revision: Union[str, None] = "25224c748ae0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_STATISTICS_SETTINGS_ACTIONS: list[str] = ["view", "update"]


def upgrade() -> None:
    op.create_table(
        "statistics_settings",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("base_url", sa.String(length=256), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
    )

    op.create_table(
        "statistics_recalc_status",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="idle"),
        sa.Column("triggered_by", sa.String(length=32), nullable=True),
        sa.Column("test_run_id", sa.String(length=64), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
    )

    permissions = sa.table(
        "entity_permissions",
        sa.column("id", sa.String),
        sa.column("entity_type", sa.String),
        sa.column("role", sa.String),
        sa.column("action", sa.String),
    )
    op.bulk_insert(
        permissions,
        [
            {
                "id": f"prm_{uuid4().hex}",
                "entity_type": "statistics_settings",
                "role": "admin",
                "action": action,
            }
            for action in _STATISTICS_SETTINGS_ACTIONS
        ],
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "DELETE FROM entity_permissions WHERE entity_type = 'statistics_settings'"
        )
    )
    op.drop_table("statistics_recalc_status")
    op.drop_table("statistics_settings")
