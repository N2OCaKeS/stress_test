"""prepare-for-test test account credential reference

Revision ID: e3b9c1d74a26
Revises: ce6aafc8bdbf
Create Date: 2026-09-24 00:00:00.000000

`prepare-for-test` принимает необязательную ссылку
`test_account_credential_id` на тестовую учётку отдела в secret_service.
С ней пайплайн ставит на стенд логин, пароль и публичный ключ из credential
(легаси — общий `u`/`srv_pass`, `emm/allta_app_full/libs/liballta.py:113-114`),
без неё — прежние случайные пароль и ключ. Колонка nullable, существующие
запросы остаются на прежнем пути.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e3b9c1d74a26"
down_revision: Union[str, None] = "ce6aafc8bdbf"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "server_prepare_for_test_requests",
        sa.Column("test_account_credential_id", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("server_prepare_for_test_requests", "test_account_credential_id")
