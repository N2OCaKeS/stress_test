"""Reference ACS credentials in secret_service; retain legacy until migration."""
from alembic import op
import sqlalchemy as sa

revision = "d5f02b8a4e79"
down_revision = "c4e91a7f3d68"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("acs_settings", sa.Column("credential_id", sa.String(64), nullable=True))
    op.add_column("acs_settings", sa.Column("migration_id", sa.String(64), nullable=True))
    op.add_column("acs_settings", sa.Column("migration_owner_dept_id", sa.String(64), nullable=True))


def downgrade():
    op.drop_column("acs_settings", "migration_owner_dept_id")
    op.drop_column("acs_settings", "migration_id")
    op.drop_column("acs_settings", "credential_id")
