"""add reencrypt_outbox_entries

Proactive re-encrypt очередь для credentials. До этого re-encrypt работал
только через lazy-путь (на reveal). На «холодных» credential'ах
`migration_status.remaining_legacy` не падал до нуля — ротационный скрипт
не мог дропнуть `SECRET_ENCRYPTION_KEY__v<old>`. Outbox принимает seed после
ротации, batch-процессор перешифровывает короткими транзакциями.

Schema:

* PK `id String(64)` — `rox_<32 hex>`.
* `credential_id` → `credentials.id` (CASCADE) — hard-delete'нутая cred'а
  снесёт outbox-row.
* `source_version`, `target_version` — для аудита.
* `status` — pending | done | error, CHECK constraint.
* `attempts`, `error_message`, `seeded_at`, `completed_at`.
* Partial UNIQUE по `credential_id WHERE status = 'pending'` — repeat seed
  не дублирует активные задачи.

Revision ID: e5a7b9f2c143
Revises: d4c6f8e3b921
Create Date: 2026-06-09 12:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text


revision: str = "e5a7b9f2c143"
down_revision: Union[str, None] = "d4c6f8e3b921"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "reencrypt_outbox_entries",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("credential_id", sa.String(length=64), nullable=False),
        sa.Column("source_version", sa.Integer(), nullable=False),
        sa.Column("target_version", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column(
            "attempts",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "seeded_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "completed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["credential_id"],
            ["credentials.id"],
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'done', 'error')",
            name="ck_reencrypt_outbox_status",
        ),
        sa.CheckConstraint(
            "error_message IS NULL OR length(error_message) <= 4096",
            name="ck_reencrypt_outbox_error_len",
        ),
    )
    op.create_index(
        "ix_reencrypt_outbox_status",
        "reencrypt_outbox_entries",
        ["status"],
        unique=False,
    )
    op.create_index(
        "ix_reencrypt_outbox_credential_id",
        "reencrypt_outbox_entries",
        ["credential_id"],
        unique=False,
    )
    op.create_index(
        "uq_reencrypt_outbox_pending_per_cred",
        "reencrypt_outbox_entries",
        ["credential_id"],
        unique=True,
        postgresql_where=text("status = 'pending'"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_reencrypt_outbox_pending_per_cred",
        table_name="reencrypt_outbox_entries",
    )
    op.drop_index(
        "ix_reencrypt_outbox_credential_id",
        table_name="reencrypt_outbox_entries",
    )
    op.drop_index(
        "ix_reencrypt_outbox_status",
        table_name="reencrypt_outbox_entries",
    )
    op.drop_table("reencrypt_outbox_entries")
