"""Reference host-control SSH key in secret_service; retain legacy until migration."""
from alembic import op
import sqlalchemy as sa

revision = "a1c7e3f9b2d4"
down_revision = "d5f02b8a4e79"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("host_services_settings", sa.Column("credential_id", sa.String(64), nullable=True))
    op.add_column("host_services_settings", sa.Column("migration_id", sa.String(64), nullable=True))


def downgrade():
    op.drop_column("host_services_settings", "migration_id")
    op.drop_column("host_services_settings", "credential_id")
