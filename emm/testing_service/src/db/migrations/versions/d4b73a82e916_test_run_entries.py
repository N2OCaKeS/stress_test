"""Persist campaign composition and link existing retry chains."""

from alembic import op
import sqlalchemy as sa

revision = "d4b73a82e916"
down_revision = "c8a4f2d913e0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "test_runs",
        sa.Column(
            "composition_source",
            sa.String(32),
            nullable=False,
            server_default="legacy_queue",
        ),
    )
    op.create_table(
        "test_run_entries",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "test_run_id",
            sa.String(64),
            sa.ForeignKey("test_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("stand_id", sa.String(64), nullable=False),
        sa.Column("test_id", sa.String(64), nullable=False),
        sa.Column("test_code", sa.String(64), nullable=False),
        sa.Column("test_name", sa.String(256), nullable=False),
        sa.Column("enqueue_error_code", sa.String(64)),
        sa.Column("enqueue_error", sa.String(2048)),
    )
    op.create_index(
        "ix_test_run_entries_test_run_id", "test_run_entries", ["test_run_id"]
    )
    op.add_column(
        "queue_items", sa.Column("test_run_entry_id", sa.String(64), nullable=True)
    )
    op.create_foreign_key(
        "fk_queue_items_test_run_entry",
        "queue_items",
        "test_run_entries",
        ["test_run_entry_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_queue_items_test_run_entry_id", "queue_items", ["test_run_entry_id"]
    )
    op.execute("""
        WITH RECURSIVE chains AS (
            SELECT q.id, q.id AS root_id, q.test_run_id FROM queue_items q
            WHERE q.test_run_id IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM queue_items parent
                WHERE parent.id = q.retry_of_id AND parent.test_run_id = q.test_run_id
            )
            UNION ALL
            SELECT q.id, c.root_id, c.test_run_id FROM queue_items q
            JOIN chains c ON q.retry_of_id = c.id AND q.test_run_id = c.test_run_id
        )
        INSERT INTO test_run_entries (id, test_run_id, stand_id, test_id, test_code, test_name)
        SELECT 'entry_' || md5(q.id), q.test_run_id, q.stand_id, q.test_id, t.code, t.full_name
        FROM queue_items q JOIN test_definitions t ON t.id = q.test_id
        WHERE q.id IN (SELECT DISTINCT root_id FROM chains)
    """)
    op.execute("""
        WITH RECURSIVE chains AS (
            SELECT q.id, e.id AS entry_id FROM queue_items q
            JOIN test_run_entries e ON e.id = 'entry_' || md5(q.id)
            UNION ALL
            SELECT q.id, c.entry_id FROM queue_items q JOIN chains c ON q.retry_of_id = c.id
            JOIN test_run_entries e ON e.id = c.entry_id AND e.test_run_id = q.test_run_id
        )
        UPDATE queue_items q SET test_run_entry_id = c.entry_id FROM chains c WHERE c.id = q.id
    """)
    op.execute("""
        UPDATE test_runs run SET status = summary.status
        FROM (
            SELECT q.test_run_id, CASE
                WHEN bool_or(q.state IN ('queued','preparing','ready','running')) THEN 'running'
                WHEN bool_and(q.state = 'succeeded') THEN 'succeeded'
                WHEN bool_and(q.state = 'failed') THEN 'failed'
                ELSE 'partially_failed' END AS status
            FROM queue_items q
            WHERE q.test_run_id IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM queue_items child WHERE child.retry_of_id = q.id
                AND child.test_run_id = q.test_run_id AND child.test_id = q.test_id
                AND child.stand_id = q.stand_id AND child.debug_mode = q.debug_mode
            )
            GROUP BY q.test_run_id
        ) summary WHERE run.id = summary.test_run_id
    """)


def downgrade() -> None:
    op.drop_column("queue_items", "test_run_entry_id")
    op.drop_table("test_run_entries")
    op.drop_column("test_runs", "composition_source")
