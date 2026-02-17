"""add rbac roles groups permissions

Revision ID: 09c5d6b2f3e1
Revises: ceb2e953c5c9
Create Date: 2026-02-13 21:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "09c5d6b2f3e1"
down_revision: Union[str, Sequence[str], None] = "ceb2e953c5c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "permissions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("description", sa.String(length=255), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_permissions_id"), "permissions", ["id"], unique=False)
    op.create_index(op.f("ix_permissions_code"), "permissions", ["code"], unique=True)

    op.create_table(
        "roles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("description", sa.String(length=255), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_roles_id"), "roles", ["id"], unique=False)
    op.create_index(op.f("ix_roles_name"), "roles", ["name"], unique=True)

    op.create_table(
        "groups",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("description", sa.String(length=255), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_groups_id"), "groups", ["id"], unique=False)
    op.create_index(op.f("ix_groups_name"), "groups", ["name"], unique=True)

    op.create_table(
        "role_permissions",
        sa.Column("role_id", sa.Integer(), nullable=False),
        sa.Column("permission_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["permission_id"], ["permissions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["role_id"], ["roles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("role_id", "permission_id"),
    )
    op.create_table(
        "group_permissions",
        sa.Column("group_id", sa.Integer(), nullable=False),
        sa.Column("permission_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["group_id"], ["groups.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["permission_id"], ["permissions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("group_id", "permission_id"),
    )
    op.create_table(
        "user_groups",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("group_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["group_id"], ["groups.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "group_id"),
    )

    op.add_column("users", sa.Column("role_id", sa.Integer(), nullable=True))
    op.create_index(op.f("ix_users_role_id"), "users", ["role_id"], unique=False)
    op.create_foreign_key(
        "fk_users_role_id_roles",
        "users",
        "roles",
        ["role_id"],
        ["id"],
    )

    op.execute(
        """
        INSERT INTO permissions (code, description)
        VALUES
            ('docker', 'Can push artifacts to docker registry'),
            ('portainer', 'Can access Portainer'),
            ('devpi', 'Can upload artifacts to DevPI');
        """
    )
    op.execute(
        """
        INSERT INTO roles (name, description)
        VALUES
            ('admin', 'Administrator role with all permissions'),
            ('user', 'Default user role'),
            ('guest', 'Guest role without privileged access');
        """
    )
    op.execute(
        """
        INSERT INTO groups (name, description)
        VALUES
            ('guest', 'Default group for all users'),
            ('docker', 'Users allowed to push to docker registry'),
            ('portainer', 'Users allowed to access Portainer'),
            ('devpi', 'Users allowed to upload to DevPI');
        """
    )

    # role permissions
    op.execute(
        """
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id
        FROM roles r
        CROSS JOIN permissions p
        WHERE r.name = 'admin';
        """
    )
    op.execute(
        """
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id
        FROM roles r
        JOIN permissions p ON p.code = 'portainer'
        WHERE r.name = 'user';
        """
    )

    # group permissions
    op.execute(
        """
        INSERT INTO group_permissions (group_id, permission_id)
        SELECT g.id, p.id
        FROM groups g
        JOIN permissions p ON p.code = 'docker'
        WHERE g.name = 'docker';
        """
    )
    op.execute(
        """
        INSERT INTO group_permissions (group_id, permission_id)
        SELECT g.id, p.id
        FROM groups g
        JOIN permissions p ON p.code = 'portainer'
        WHERE g.name = 'portainer';
        """
    )
    op.execute(
        """
        INSERT INTO group_permissions (group_id, permission_id)
        SELECT g.id, p.id
        FROM groups g
        JOIN permissions p ON p.code = 'devpi'
        WHERE g.name = 'devpi';
        """
    )

    # backfill user roles (keep compatibility with legacy is_admin)
    op.execute(
        """
        UPDATE users u
        SET role_id = r.id
        FROM roles r
        WHERE u.is_admin = TRUE
          AND r.name = 'admin';
        """
    )
    op.execute(
        """
        UPDATE users u
        SET role_id = r.id
        FROM roles r
        WHERE u.role_id IS NULL
          AND r.name = 'user';
        """
    )

    # every user gets default guest group
    op.execute(
        """
        INSERT INTO user_groups (user_id, group_id)
        SELECT u.id, g.id
        FROM users u
        CROSS JOIN groups g
        WHERE g.name = 'guest';
        """
    )

    op.alter_column("users", "role_id", nullable=False)


def downgrade() -> None:
    op.alter_column("users", "role_id", nullable=True)
    op.drop_constraint("fk_users_role_id_roles", "users", type_="foreignkey")
    op.drop_index(op.f("ix_users_role_id"), table_name="users")
    op.drop_column("users", "role_id")

    op.drop_table("user_groups")
    op.drop_table("group_permissions")
    op.drop_table("role_permissions")

    op.drop_index(op.f("ix_groups_name"), table_name="groups")
    op.drop_index(op.f("ix_groups_id"), table_name="groups")
    op.drop_table("groups")

    op.drop_index(op.f("ix_roles_name"), table_name="roles")
    op.drop_index(op.f("ix_roles_id"), table_name="roles")
    op.drop_table("roles")

    op.drop_index(op.f("ix_permissions_code"), table_name="permissions")
    op.drop_index(op.f("ix_permissions_id"), table_name="permissions")
    op.drop_table("permissions")
