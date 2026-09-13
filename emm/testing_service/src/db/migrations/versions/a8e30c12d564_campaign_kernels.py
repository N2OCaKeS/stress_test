"""Freeze every kernel in campaign composition."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "a8e30c12d564"
down_revision = "f7d29b01c453"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("test_runs", sa.Column("kernels", JSONB(), nullable=False, server_default="[]"))
    op.add_column("test_run_entries", sa.Column("kernel", sa.String(64)))
    op.execute("UPDATE test_runs SET kernels = jsonb_build_array(kernel)")
    op.execute("UPDATE test_run_entries e SET kernel = r.kernel FROM test_runs r WHERE e.test_run_id = r.id")


def downgrade():
    op.drop_column("test_run_entries", "kernel")
    op.drop_column("test_runs", "kernels")
