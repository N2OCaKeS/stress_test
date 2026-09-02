"""add_user_groups

Revision ID: d7e8f9a0b1c2
Revises: c4f1a2b3d5e6
Create Date: 2026-04-21 01:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'd7e8f9a0b1c2'
down_revision: Union[str, None] = 'c4f1a2b3d5e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'user_groups',
        sa.Column('id', sa.String(length=64), nullable=False),
        sa.Column('name', sa.String(length=128), nullable=False),
        sa.Column('display_name', sa.String(length=256), nullable=False),
        sa.Column('description', sa.String(length=1024), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_by', sa.String(length=64), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name', name='uq_user_group_name'),
    )

    op.create_table(
        'user_group_memberships',
        sa.Column('id', sa.String(length=64), nullable=False),
        sa.Column('group_id', sa.String(length=64), nullable=False),
        sa.Column('user_id', sa.String(length=64), nullable=False),
        sa.Column('added_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('added_by', sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(['group_id'], ['user_groups.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('group_id', 'user_id', name='uq_group_membership'),
    )
    op.create_index('ix_user_group_memberships_group_id', 'user_group_memberships', ['group_id'])
    op.create_index('ix_user_group_memberships_user_id', 'user_group_memberships', ['user_id'])

    op.create_table(
        'group_service_access',
        sa.Column('id', sa.String(length=64), nullable=False),
        sa.Column('group_id', sa.String(length=64), nullable=False),
        sa.Column('service_name', sa.String(length=128), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('granted_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('granted_by', sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(['group_id'], ['user_groups.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['service_name'], ['platform_services.service_name'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('group_id', 'service_name', name='uq_group_service_access'),
    )
    op.create_index('ix_group_service_access_group_id', 'group_service_access', ['group_id'])

    op.create_table(
        'group_service_roles',
        sa.Column('id', sa.String(length=64), nullable=False),
        sa.Column('group_id', sa.String(length=64), nullable=False),
        sa.Column('service_name', sa.String(length=128), nullable=False),
        sa.Column('role', sa.String(length=64), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('assigned_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('assigned_by', sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(['group_id'], ['user_groups.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['service_name'], ['platform_services.service_name'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('group_id', 'service_name', 'role', name='uq_group_service_role'),
    )
    op.create_index('ix_group_service_roles_group_id', 'group_service_roles', ['group_id'])


def downgrade() -> None:
    op.drop_index('ix_group_service_roles_group_id', table_name='group_service_roles')
    op.drop_table('group_service_roles')
    op.drop_index('ix_group_service_access_group_id', table_name='group_service_access')
    op.drop_table('group_service_access')
    op.drop_index('ix_user_group_memberships_user_id', table_name='user_group_memberships')
    op.drop_index('ix_user_group_memberships_group_id', table_name='user_group_memberships')
    op.drop_table('user_group_memberships')
    op.drop_table('user_groups')
