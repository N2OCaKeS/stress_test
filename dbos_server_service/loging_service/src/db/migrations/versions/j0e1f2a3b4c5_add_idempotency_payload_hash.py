"""add idempotency_payload_hash to audit_events

Revision ID: j0e1f2a3b4c5
Revises: i9d0e1f2a3b4
Create Date: 2026-06-01 03:00:00.000000

Idempotency poisoning защита: на `(service, idempotency_key)` UNIQUE row
до сих пор INSERT…ON CONFLICT DO NOTHING молча возвращал старый row, не
проверяя, что payload идентичен. Это позволяло держателю `SERVICE_API_KEY`
заранее «застолбить» idempotency_key с фейковым payload'ом — легитимный
сервис на ретрае получал чужое (атакующего) событие в БД, а свой реальный
audit-row тихо терял.

Колонка `idempotency_payload_hash` (SHA-256 от canonical-JSON payload'а)
позволяет на CONFLICT-ветке сравнить hash старого row и нового запроса:
совпало → idempotent replay (200, существующий row); разошлось → 409
IDEMPOTENCY_KEY_CONFLICT + self-audit WARNING.

Колонка nullable: legacy row'ы без idempotency_key (и текущие — до
backfill'а) hash не имеют. На INSERT с idempotency_key writer всегда
выставляет hash; на legacy ingest (idempotency_key is None) hash тоже
NULL.
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "j0e1f2a3b4c5"
down_revision: Union[str, None] = "i9d0e1f2a3b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "audit_events",
        sa.Column("idempotency_payload_hash", sa.String(64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("audit_events", "idempotency_payload_hash")
