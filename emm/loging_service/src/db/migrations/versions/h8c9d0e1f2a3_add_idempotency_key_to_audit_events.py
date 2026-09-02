"""add idempotency_key to audit_events

Revision ID: h8c9d0e1f2a3
Revises: g7b8c9d0e1f2
Create Date: 2026-05-20 14:07:01.000000

Ingest deduplication.

После outbox-pattern в server_worker audit-event POSTs may be
retried by ``audit_outbox_publisher`` on transient network failure. Without
a deduplication key, the same logical event is written twice to
``audit_events``. We add an opaque ``idempotency_key`` column scoped to
``(service, idempotency_key)`` (each service owns its own key namespace —
two services may legitimately pick the same string for unrelated events).

The UNIQUE constraint uses a **partial index** (``WHERE idempotency_key IS
NOT NULL``) so legacy callers that don't send the key are not forced into a
single-row-per-service limit. ``ON CONFLICT (service, idempotency_key) DO
NOTHING`` in the repository layer becomes safe.
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "h8c9d0e1f2a3"
down_revision: Union[str, None] = "g7b8c9d0e1f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "audit_events",
        sa.Column("idempotency_key", sa.String(128), nullable=True),
    )
    # Partial UNIQUE index: only enforce when the key is set. NULL keys
    # collide infinitely (legacy + one-shot ingest), which is the desired
    # backward-compat behaviour.
    op.create_index(
        "uq_audit_events_service_idempotency_key",
        "audit_events",
        ["service", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_audit_events_service_idempotency_key", table_name="audit_events"
    )
    op.drop_column("audit_events", "idempotency_key")
