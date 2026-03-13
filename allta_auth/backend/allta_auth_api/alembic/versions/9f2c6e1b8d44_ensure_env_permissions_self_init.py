"""ensure env permissions self init

Revision ID: 9f2c6e1b8d44
Revises: 6b9c1d4e2a77
Create Date: 2026-03-10 19:05:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "9f2c6e1b8d44"
down_revision: Union[str, Sequence[str], None] = "6b9c1d4e2a77"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Permissions that are configured via env examples across projects.
    op.execute(
        """
        INSERT INTO permissions (code, description)
        VALUES
            ('config.tokens', 'Can read tokens payload from Config API'),
            ('server.manage', 'Can manage servers'),
            ('vm.manage', 'Can manage virtual machines'),
            ('server.snapshot_passwords.read', 'Can read snapshot passwords'),
            ('server.snapshot_passwords.write', 'Can manage snapshot passwords')
        ON CONFLICT (code) DO NOTHING;
        """
    )

    op.execute(
        """
        INSERT INTO groups (name, description)
        VALUES
            ('config_tokens', 'Users allowed to read Config API tokens'),
            ('infra_managers', 'Users allowed to manage servers and VMs'),
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
        JOIN permissions p ON p.code = 'config.tokens'
        WHERE g.name = 'config_tokens'
        ON CONFLICT DO NOTHING;
        """
    )

    op.execute(
        """
        INSERT INTO group_permissions (group_id, permission_id)
        SELECT g.id, p.id
        FROM groups g
        JOIN permissions p ON p.code IN ('server.manage', 'vm.manage')
        WHERE g.name = 'infra_managers'
        ON CONFLICT DO NOTHING;
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
        JOIN permissions p ON p.code IN (
            'config.tokens',
            'server.manage',
            'vm.manage',
            'server.snapshot_passwords.read',
            'server.snapshot_passwords.write'
        )
        WHERE r.name = 'admin'
        ON CONFLICT DO NOTHING;
        """
    )


def downgrade() -> None:
    # Intentionally no-op: permissions/groups are shared with previous migrations
    # and may be actively used in running environments.
    pass
