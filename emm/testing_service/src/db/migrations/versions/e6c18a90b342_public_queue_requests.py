"""Idempotent public launches and an explicit STP execution reference."""

from alembic import op
import sqlalchemy as sa

revision = "e6c18a90b342"
down_revision = "d4b73a82e916"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("queue_items", sa.Column("client_request_id", sa.String(128)))
    op.add_column("queue_items", sa.Column("request_fingerprint", sa.String(64)))
    op.add_column("queue_items", sa.Column("stp_test_run_id", sa.String(64)))
    op.create_unique_constraint(
        "uq_queue_items_client_request",
        "queue_items",
        ["created_by", "client_request_id"],
    )


def downgrade():
    op.drop_constraint("uq_queue_items_client_request", "queue_items", type_="unique")
    for column in ("stp_test_run_id", "request_fingerprint", "client_request_id"):
        op.drop_column("queue_items", column)
