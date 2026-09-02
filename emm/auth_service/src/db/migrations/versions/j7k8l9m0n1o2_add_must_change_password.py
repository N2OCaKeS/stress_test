"""add must_change_password to users

Revision ID: j7k8l9m0n1o2
Revises: i6j7k8l9m0n1
Create Date: 2026-06-09 00:00:00.000000

Колонка `users.must_change_password` (boolean, default False, NOT NULL).

Семантика: TRUE = юзер должен сменить пароль через `/users/me/password` до того,
как middleware пустит его на остальные endpoint'ы. FALSE = обычный режим.

Кто ставит TRUE:
  * `bootstrap_admin` при первом создании account_admin'а из ENV;
  * `user_service.create_user` (dep_admin создал юзера с временным паролем);
  * `user_service.reset_password` (админ сбросил пароль чужому юзеру);
  * seed-скрипты при первом создании loging_admin/loging_reader.

Кто ставит FALSE:
  * `user_service.change_own_password` после успешной смены.

`server_default=false` нужен для обратной совместимости — existing rows
получают FALSE автоматически, без миграции данных. Это значит, что уже
созданные юзера до накатывания миграции пройдут без блокировки на смену
пароля; форсировать смену для legacy-аккаунтов — отдельная админская задача.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "j7k8l9m0n1o2"
down_revision: Union[str, None] = "i6j7k8l9m0n1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "must_change_password",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "must_change_password")
