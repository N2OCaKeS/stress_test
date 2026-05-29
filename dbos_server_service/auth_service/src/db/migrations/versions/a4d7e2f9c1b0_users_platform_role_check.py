"""users.platform_role CHECK constraint

Revision ID: a4d7e2f9c1b0
Revises: f3a4b5c6d7e8
Create Date: 2026-05-29 22:30:00.000000

"""
from typing import Sequence, Union

from alembic import op


revision: str = "a4d7e2f9c1b0"
down_revision: Union[str, None] = "f3a4b5c6d7e8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Список допустимых значений зеркалит `core.constants.PlatformRole`.
# Если в enum добавляется новое значение — пишется новая миграция,
# которая ALTER'ит CHECK; жёсткая привязка к строкам сделана сознательно,
# чтобы CHECK не плыл при рефакторингах кода.
_ALLOWED = ("account_admin", "department_admin", "loging_admin", "loging_reader")


def upgrade() -> None:
    values = ", ".join(f"'{v}'" for v in _ALLOWED)
    op.create_check_constraint(
        "ck_users_platform_role",
        "users",
        f"platform_role IS NULL OR platform_role IN ({values})",
    )


def downgrade() -> None:
    op.drop_constraint("ck_users_platform_role", "users", type_="check")
