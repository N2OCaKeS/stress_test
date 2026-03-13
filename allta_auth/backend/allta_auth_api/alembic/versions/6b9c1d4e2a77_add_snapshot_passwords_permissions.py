"""add snapshot passwords permissions

Revision ID: 6b9c1d4e2a77
Revises: f2a41d6c9e10
Create Date: 2026-03-10 16:47:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "6b9c1d4e2a77"
down_revision: Union[str, Sequence[str], None] = "f2a41d6c9e10"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO permissions (code, description)
        VALUES
            ('server.snapshot_passwords.read', 'Can read snapshot passwords'),
            ('server.snapshot_passwords.write', 'Can manage snapshot passwords')
        ON CONFLICT (code) DO NOTHING;
        """
    )
    op.execute(
        """
        INSERT INTO groups (name, description)
        VALUES
            ('snapshot_passwords_admins', 'Admins for snapshot passwords management'),
            ('snapshot_passwords_readers', 'Bots/users allowed to read snapshot passwords')
        ON CONFLICT (name) DO NOTHING;
        """
    )
    op.execute(
        """
        INSERT INTO group_permissions (group_id, permission_id)
        SELECT g.id, p.id
        FROM groups g
        JOIN permissions p ON p.code IN ('server.snapshot_passwords.read', 'server.snapshot_passwords.write')
        WHERE g.name = 'snapshot_passwords_admins'
        ON CONFLICT DO NOTHING;
        """
    )
    op.execute(
        """
        INSERT INTO group_permissions (group_id, permission_id)
        SELECT g.id, p.id
        FROM groups g
        JOIN permissions p ON p.code = 'server.snapshot_passwords.read'
        WHERE g.name = 'snapshot_passwords_readers'
        ON CONFLICT DO NOTHING;
        """
    )
    op.execute(
        """
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id
        FROM roles r
        JOIN permissions p ON p.code IN ('server.snapshot_passwords.read', 'server.snapshot_passwords.write')
        WHERE r.name = 'admin'
        ON CONFLICT DO NOTHING;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM role_permissions
        WHERE permission_id IN (
            SELECT id FROM permissions
            WHERE code IN ('server.snapshot_passwords.read', 'server.snapshot_passwords.write')
        );
        """
    )
    op.execute(
        """
        DELETE FROM group_permissions
        WHERE group_id IN (
            SELECT id FROM groups
            WHERE name IN ('snapshot_passwords_admins', 'snapshot_passwords_readers')
        );
        """
    )
    op.execute(
        """
        DELETE FROM groups
        WHERE name IN ('snapshot_passwords_admins', 'snapshot_passwords_readers');
        """
    )
    op.execute(
        """
        DELETE FROM permissions
        WHERE code IN ('server.snapshot_passwords.read', 'server.snapshot_passwords.write');
        """
    )
