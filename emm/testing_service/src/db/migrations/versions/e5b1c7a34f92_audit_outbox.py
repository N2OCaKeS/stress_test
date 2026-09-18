"""audit_outbox — durable очередь audit-событий

Revision ID: e5b1c7a34f92
Revises: a4d1f70b39c8
Create Date: 2026-09-18 10:00:00.000000

До этой таблицы `audit_service.emit()` стрелял HTTP'ом в loging_service
fire-and-forget и глушил любую ошибку в WARNING: недоступный (или просто
перезапускающийся) loging молча съедал событие, включая CRITICAL
`permission.grant`/`permission.revoke`. Теперь emit кладёт payload сюда, а
доставкой занимается фоновый цикл `services/audit_outbox_publisher`.

Схема повторяет одноимённую таблицу `server_worker` (payload + attempts +
last_error + next_retry_at + partial-index по неопубликованным), плюс
денормализованный `action` — чтобы застрявшее событие было видно обычным
SELECT'ом без раскопок JSONB.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e5b1c7a34f92"
down_revision: Union[str, None] = "c1f9b48e7a30"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "audit_outbox",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("action", sa.String(length=128), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_audit_outbox_action", "audit_outbox", ["action"])
    # Partial-index по неопубликованным: дренаж сканирует только их, а
    # ключ (next_retry_at, created_at) кладёт фильтр «можно ретраить»
    # прямо в b-tree.
    op.create_index(
        "ix_audit_outbox_unpublished_retry",
        "audit_outbox",
        ["next_retry_at", "created_at"],
        postgresql_where=sa.text("published_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_audit_outbox_unpublished_retry", table_name="audit_outbox")
    op.drop_index("ix_audit_outbox_action", table_name="audit_outbox")
    op.drop_table("audit_outbox")
