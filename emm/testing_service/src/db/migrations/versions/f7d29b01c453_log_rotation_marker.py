"""Keep log rotation visible in attempt history."""
from alembic import op
import sqlalchemy as sa

revision = "f7d29b01c453"
down_revision = "e6c18a90b342"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("queue_items", sa.Column("log_rotated_at", sa.DateTime(timezone=True)))


def downgrade():
    op.drop_column("queue_items", "log_rotated_at")
