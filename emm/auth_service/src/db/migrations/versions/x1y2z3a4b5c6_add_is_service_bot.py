"""add is_service_bot to bot_accounts

Revision ID: x1y2z3a4b5c6
Revises: w0x1y2z3a4b5
Create Date: 2026-09-07 00:00:00.000000

Колонка `bot_accounts.is_service_bot` (boolean, default False, NOT NULL).

Семантика: TRUE — платформенный сервис-бот (`server_worker`, `testing_service`
и подобные), заводится ТОЛЬКО bootstrap-кодом на старте сервиса
(`bootstrap_service.bootstrap_worker_bot` / `bootstrap_testing_service_bot`).
FALSE — обычный бот, которого через `POST /bots` заводит себе `dep_admin`.

Флаг не выставляется через обычный bot CRUD API — `BotCreate`/`BotUpdate`
схемы его не принимают, insert/update всегда берут дефолт. Используется
`secret_service` для scope="service" credential (см. новую ветку доступа
`_check_service` в `access_service.py`): любой сервис-бот получает read/reveal
на такую креду независимо от отдела, обычные боты — только по обычной
department-видимости.

`server_default=false` — существующие боты получают FALSE автоматически;
bootstrap-функции сами доносят флаг до True на следующем рестарте для уже
заведённых платформенных ботов.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "x1y2z3a4b5c6"
down_revision: Union[str, None] = "w0x1y2z3a4b5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "bot_accounts",
        sa.Column(
            "is_service_bot",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("bot_accounts", "is_service_bot")
