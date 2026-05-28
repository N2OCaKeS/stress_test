"""add deleted_at to audit_rules

Revision ID: i9d0e1f2a3b4
Revises: h8c9d0e1f2a3
Create Date: 2026-05-29 12:00:00.000000

Cross-worker invalidation для DELETE правил аудита.

`_RuleCache` (services/rule_service.py) триггерит reload по `MAX(updated_at)`
из таблицы. SQL `DELETE FROM audit_rules WHERE id=...` не меняет MAX:
оставшиеся row хранят прежние `updated_at`. Воркер A удалил SUPPRESS-правило,
воркер B/C продолжают применять его до TTL=30s — окно потерянных событий.

Решение: soft-delete через `deleted_at`. `delete_rule` ставит timestamp
и поднимает `updated_at`; row остаётся в таблице, MAX(updated_at)
растёт → остальные воркеры инвалидируют кеш и видят новое состояние.
`get_active_sorted` фильтрует `deleted_at IS NULL`. `get_by_id` тоже —
повторные операции над удалённым правилом возвращают 404.
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "i9d0e1f2a3b4"
down_revision: Union[str, None] = "h8c9d0e1f2a3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "audit_rules",
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("audit_rules", "deleted_at")
