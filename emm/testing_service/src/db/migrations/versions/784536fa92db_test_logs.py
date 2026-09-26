"""test logs, segments and blobs

Revision ID: 784536fa92db
Revises: bc059aaa2aa1
Create Date: 2026-09-09 21:00:00.000000

Пятый домен testing_service (§2.6, §8 плана миграции):
хранение логов прогонов — модель данных + приёмный internal-контракт для
`testing_worker`.

`test_logs` — метаданные одного лога (один на `queue_item`). `queue_item_id`
— `ON DELETE SET NULL`: лог обязан пережить возможное будущее удаление
`queue_item`а (сейчас такой операции в сервисе нет вовсе), поэтому связь не
блокирует и не каскадирует. `stand_id`/`test_id`/`os_version_major`/`rc`/
`kernel` — снэпшот-метки без FK (см. `models/test_log.py`).

`test_log_segments` — навигация поверх текста лога (чекпоинты/команды),
`ON DELETE CASCADE` от `test_logs` — сегменты не существуют без своего лога.

`test_log_blobs` — сам текст лога, 1:1 к `test_logs` (`log_id` одновременно
PK и FK, `ON DELETE CASCADE`). Отдельная таблица — обычный SELECT
списка/карточки лога не должен таскать с собой потенциально длинный TEXT.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "784536fa92db"
down_revision: Union[str, None] = "bc059aaa2aa1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "test_logs",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column(
            "queue_item_id", sa.String(length=64),
            sa.ForeignKey("queue_items.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("stand_id", sa.String(length=64), nullable=False),
        sa.Column("test_id", sa.String(length=64), nullable=False),
        sa.Column("os_version_major", sa.String(length=32), nullable=True),
        sa.Column("rc", sa.String(length=64), nullable=True),
        sa.Column("kernel", sa.String(length=64), nullable=True),
        sa.Column("internal_path", sa.String(length=512), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.Column("protected", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_test_logs_queue_item_id", "test_logs", ["queue_item_id"], unique=True,
    )
    op.create_index("ix_test_logs_stand_id", "test_logs", ["stand_id"])
    op.create_index("ix_test_logs_test_id", "test_logs", ["test_id"])
    op.create_index(
        "ix_test_logs_branch_rc", "test_logs", ["os_version_major", "rc"],
    )
    op.create_index(
        "ix_test_logs_protected_created_at", "test_logs", ["protected", "created_at"],
    )

    op.create_table(
        "test_log_segments",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column(
            "log_id", sa.String(length=64),
            sa.ForeignKey("test_logs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("label", sa.String(length=256), nullable=False),
        sa.Column("command_text_masked", sa.String(length=4096), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("byte_offset_start", sa.Integer(), nullable=False),
        sa.Column("byte_offset_end", sa.Integer(), nullable=True),
    )
    op.create_index("ix_test_log_segments_log_id", "test_log_segments", ["log_id"])
    op.create_index(
        "ix_test_log_segments_log_id_position", "test_log_segments",
        ["log_id", "position"], unique=True,
    )
    op.create_index("ix_test_log_segments_status", "test_log_segments", ["status"])

    op.create_table(
        "test_log_blobs",
        sa.Column(
            "log_id", sa.String(length=64),
            sa.ForeignKey("test_logs.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_table("test_log_blobs")
    op.drop_index("ix_test_log_segments_status", table_name="test_log_segments")
    op.drop_index("ix_test_log_segments_log_id_position", table_name="test_log_segments")
    op.drop_index("ix_test_log_segments_log_id", table_name="test_log_segments")
    op.drop_table("test_log_segments")
    op.drop_index("ix_test_logs_protected_created_at", table_name="test_logs")
    op.drop_index("ix_test_logs_branch_rc", table_name="test_logs")
    op.drop_index("ix_test_logs_test_id", table_name="test_logs")
    op.drop_index("ix_test_logs_stand_id", table_name="test_logs")
    op.drop_index("ix_test_logs_queue_item_id", table_name="test_logs")
    op.drop_table("test_logs")
