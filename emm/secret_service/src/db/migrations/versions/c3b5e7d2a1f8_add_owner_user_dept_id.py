"""add owner_user_dept_id to credentials

Колонка хранит denormalized-копию dept'а пользователя-владельца на момент
создания personal-кред. Нужна для проверки RoleACL в access_service: ACL на
personal cred должна жить в dep'е владельца, а не в dep'е актора.

Для department/cross_department-кред поле остаётся NULL — owner_dept_id уже
закрывает эту роль.

Revision ID: c3b5e7d2a1f8
Revises: b2a4d9e1c815
Create Date: 2026-06-08 17:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c3b5e7d2a1f8"
down_revision: Union[str, None] = "b2a4d9e1c815"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "credentials",
        sa.Column("owner_user_dept_id", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "ix_credentials_owner_user_dept",
        "credentials",
        ["owner_user_dept_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_credentials_owner_user_dept", table_name="credentials")
    op.drop_column("credentials", "owner_user_dept_id")
