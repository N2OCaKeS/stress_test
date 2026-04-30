"""add_fk_docker_registry_department

Revision ID: 6dd91b9272e2
Revises: a36352b55ca0
Create Date: 2026-04-18 17:39:30.662617

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '6dd91b9272e2'
down_revision: Union[str, None] = 'a36352b55ca0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_FK_NAME = "fk_dept_docker_registry_dept_id"


def upgrade() -> None:
    op.create_foreign_key(_FK_NAME, 'department_docker_registry', 'departments', ['department_id'], ['id'], ondelete='CASCADE')


def downgrade() -> None:
    op.drop_constraint(_FK_NAME, 'department_docker_registry', type_='foreignkey')
