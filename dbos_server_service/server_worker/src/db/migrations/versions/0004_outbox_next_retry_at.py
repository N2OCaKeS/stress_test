"""audit_outbox.next_retry_at: per-row exponential backoff for publisher

Revision ID: 0004_outbox_next_retry_at
Revises: 0003_durable_retry_heartbeats
Create Date: 2026-05-29 00:00:00.000000

Зачем:

После перехода publisher'а на per-row commit одна 5xx-row держала свой
backoff только через position в выборке (мы её exclude'или из текущего
прохода). На следующем тике она снова попадала в выборку, и если loging
ещё лежит — publisher долбил её каждые `_POLL_INTERVAL_SECONDS`,
выжигая HTTP-лимиты loging_service и наращивая `attempts` без пауз.

Колонка `next_retry_at` хранит «не раньше этого момента берём row
обратно в pickup». Publisher на failure ставит её в `now() +
2^attempts seconds` (capped), на success — оставляет NULL и row
помечается published. SELECT для выборки фильтрует `next_retry_at IS
NULL OR next_retry_at <= now()`, partial-index перестроен с тем же
условием — старая часть index'а (`published_at IS NULL`) остаётся.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004_outbox_next_retry_at"
down_revision: Union[str, None] = "0003_durable_retry_heartbeats"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "audit_outbox",
        sa.Column(
            "next_retry_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    # Старый partial-index по «unpublished» больше не покрывает фильтр
    # publisher'а — пересоздаём с тем же ключом, но `created_at` остаётся
    # колонкой сортировки. Условие `published_at IS NULL` сохранили — оно
    # ещё используется и cleanup'ом.
    op.create_index(
        "ix_audit_outbox_unpublished_retry",
        "audit_outbox",
        ["next_retry_at", "created_at"],
        postgresql_where=sa.text("published_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_audit_outbox_unpublished_retry", table_name="audit_outbox"
    )
    op.drop_column("audit_outbox", "next_retry_at")
