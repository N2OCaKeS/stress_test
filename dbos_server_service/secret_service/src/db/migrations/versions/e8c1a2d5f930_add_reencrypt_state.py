"""add reencrypt_state singleton

Singleton-строка состояния перешифровки, общая для всех реплик. Держит режим
дренажа outbox'а (lazy/force), флаг активного force-окна и снапшот прогресса
(remaining/throughput/eta), который self-drain loop обновляет на каждом тике.
Force-флаг обязан пережить рестарт пода, поэтому живёт в БД, а не в памяти.

Schema:

* PK `id INTEGER` с CHECK id = 1 — таблица всегда содержит ровно одну строку.
* `mode` — lazy | force.
* `force_active` — включён ли maintenance-gate.
* `remaining` / `throughput` / `eta_seconds` — снапшот прогресса.
* `started_at` / `updated_at` — тайминги.

Строка сразу засевается дефолтами (lazy, force снят).

Revision ID: e8c1a2d5f930
Revises: d1b8f3c6a924
Create Date: 2026-07-02 10:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e8c1a2d5f930"
down_revision: Union[str, None] = "d1b8f3c6a924"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "reencrypt_state",
        sa.Column("id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column(
            "mode", sa.String(length=16), server_default=sa.text("'lazy'"),
            nullable=False,
        ),
        sa.Column(
            "force_active", sa.Boolean(), server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "remaining", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "throughput", sa.Float(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "eta_seconds", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.CheckConstraint("id = 1", name="ck_reencrypt_state_singleton"),
        sa.CheckConstraint(
            "mode IN ('lazy', 'force')", name="ck_reencrypt_state_mode"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.execute(
        "INSERT INTO reencrypt_state (id, mode, force_active, remaining, "
        "throughput, eta_seconds) VALUES (1, 'lazy', false, 0, 0, 0)"
    )


def downgrade() -> None:
    op.drop_table("reencrypt_state")
