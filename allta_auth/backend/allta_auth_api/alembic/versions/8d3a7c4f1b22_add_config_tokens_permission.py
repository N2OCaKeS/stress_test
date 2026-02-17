"""add config tokens permission

Revision ID: 8d3a7c4f1b22
Revises: c4f7e1a9d2ab
Create Date: 2026-02-13 22:10:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "8d3a7c4f1b22"
down_revision: Union[str, Sequence[str], None] = "c4f7e1a9d2ab"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO permissions (code, description)
        VALUES ('config.tokens', 'Can read tokens payload from Config API')
        ON CONFLICT (code) DO NOTHING;
        """
    )
    op.execute(
        """
        INSERT INTO groups (name, description)
        VALUES ('config_tokens', 'Users allowed to read Config API tokens')
        ON CONFLICT (name) DO NOTHING;
        """
    )
    op.execute(
        """
        INSERT INTO group_permissions (group_id, permission_id)
        SELECT g.id, p.id
        FROM groups g
        JOIN permissions p ON p.code = 'config.tokens'
        WHERE g.name = 'config_tokens'
        ON CONFLICT DO NOTHING;
        """
    )
    op.execute(
        """
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id
        FROM roles r
        JOIN permissions p ON p.code = 'config.tokens'
        WHERE r.name = 'admin'
        ON CONFLICT DO NOTHING;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM role_permissions
        WHERE permission_id IN (
            SELECT id FROM permissions WHERE code = 'config.tokens'
        );
        """
    )
    op.execute(
        """
        DELETE FROM group_permissions
        WHERE permission_id IN (
            SELECT id FROM permissions WHERE code = 'config.tokens'
        );
        """
    )
    op.execute("DELETE FROM groups WHERE name = 'config_tokens';")
    op.execute("DELETE FROM permissions WHERE code = 'config.tokens';")
