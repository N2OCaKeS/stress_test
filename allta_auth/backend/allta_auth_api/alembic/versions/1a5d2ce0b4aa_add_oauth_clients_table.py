"""add oauth clients table

Revision ID: 1a5d2ce0b4aa
Revises: f2a41d6c9e10
Create Date: 2026-02-14 02:40:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "1a5d2ce0b4aa"
down_revision: Union[str, Sequence[str], None] = "f2a41d6c9e10"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "oauth_clients",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("client_id", sa.String(length=128), nullable=False),
        sa.Column("client_secret_hash", sa.String(length=255), nullable=False),
        sa.Column("display_name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.String(length=255), nullable=True),
        sa.Column("redirect_uri_prefixes", sa.Text(), nullable=False, server_default=""),
        sa.Column("required_permission", sa.String(length=64), nullable=True),
        sa.Column("default_scope", sa.String(length=255), nullable=False, server_default="profile"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_oauth_clients_id"), "oauth_clients", ["id"], unique=False)
    op.create_index(
        op.f("ix_oauth_clients_client_id"),
        "oauth_clients",
        ["client_id"],
        unique=True,
    )

    op.execute(
        """
        INSERT INTO permissions (code, description)
        VALUES
            ('flower', 'Can access Flower UI'),
            ('redis.commander', 'Can access Redis Commander UI'),
            ('docs.api', 'Can access API documentation UI')
        ON CONFLICT (code) DO NOTHING;
        """
    )

    op.execute(
        """
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id
        FROM roles r
        JOIN permissions p ON p.code IN ('flower', 'redis.commander', 'docs.api')
        WHERE r.name = 'admin'
        ON CONFLICT DO NOTHING;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM role_permissions
        WHERE permission_id IN (
            SELECT id FROM permissions WHERE code IN ('flower', 'redis.commander', 'docs.api')
        );
        """
    )
    op.execute(
        """
        DELETE FROM permissions
        WHERE code IN ('flower', 'redis.commander', 'docs.api');
        """
    )

    op.drop_index(op.f("ix_oauth_clients_client_id"), table_name="oauth_clients")
    op.drop_index(op.f("ix_oauth_clients_id"), table_name="oauth_clients")
    op.drop_table("oauth_clients")
