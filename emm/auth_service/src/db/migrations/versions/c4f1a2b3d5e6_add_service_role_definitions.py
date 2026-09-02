"""add_service_role_definitions

Revision ID: c4f1a2b3d5e6
Revises: 88515adafb2e
Create Date: 2026-04-21 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'c4f1a2b3d5e6'
down_revision: Union[str, None] = '88515adafb2e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'service_role_definitions',
        sa.Column('id', sa.String(length=64), nullable=False),
        sa.Column('service_name', sa.String(length=128), nullable=False),
        sa.Column('role_name', sa.String(length=64), nullable=False),
        sa.Column('display_name', sa.String(length=256), nullable=False),
        sa.Column('description', sa.String(length=1024), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_by', sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(['service_name'], ['platform_services.service_name'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('service_name', 'role_name', name='uq_service_role_name'),
    )
    op.create_index('ix_service_role_definitions_service_name', 'service_role_definitions', ['service_name'])


def downgrade() -> None:
    op.drop_index('ix_service_role_definitions_service_name', table_name='service_role_definitions')
    op.drop_table('service_role_definitions')
