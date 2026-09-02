"""secrets reencrypt outbox table

Revision ID: f2a1b8c9d3e4
Revises: e3f8c4b21a07
Create Date: 2026-05-29 14:00:00.000000

Outbox-таблица для постепенной фоновой ротации мастер-ключа. Раньше
worker звал `POST /internal/secrets/reencrypt_batch` синхронно: server
держал `AsyncSessionLocal()` открытым на весь decrypt-batch → encrypt-batch
→ UPDATE цикл. На крупных батчах это блокировало pool.

Новый поток: server_service одноразово сидит outbox-row'ы при смене
активной версии (`secrets_migration_service.seed_outbox`), worker клиентит
маленькими порциями через FOR UPDATE SKIP LOCKED, финализирует каждую
строку отдельной короткой транзакцией. Запросы на пул короткие.

Status — простая ENUM-строка (`pending`/`processing`/`done`/`failed`),
без native PG enum: проще downgrade'ить и расширять (alembic не любит
ALTER TYPE ADD VALUE без CASCADE).

Уникальный partial-индекс `(entity_type, entity_id) WHERE status IN
('pending','processing')` гарантирует «одна активная задача на owner-row'у»
и не блокирует повторный seed после `done`/`failed`.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f2a1b8c9d3e4"
down_revision: Union[str, None] = "e3f8c4b21a07"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "secrets_reencrypt_outbox",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("entity_type", sa.String(length=32), nullable=False),
        sa.Column("entity_id", sa.String(length=64), nullable=False),
        sa.Column("legacy_ciphertext", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default="pending",
        ),
        sa.Column(
            "attempts",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "processed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_secrets_reencrypt_outbox_status_created",
        "secrets_reencrypt_outbox",
        ["status", "created_at"],
        unique=False,
    )
    op.create_index(
        "uq_secrets_reencrypt_outbox_entity_active",
        "secrets_reencrypt_outbox",
        ["entity_type", "entity_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('pending', 'processing')"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_secrets_reencrypt_outbox_entity_active",
        table_name="secrets_reencrypt_outbox",
    )
    op.drop_index(
        "ix_secrets_reencrypt_outbox_status_created",
        table_name="secrets_reencrypt_outbox",
    )
    op.drop_table("secrets_reencrypt_outbox")
