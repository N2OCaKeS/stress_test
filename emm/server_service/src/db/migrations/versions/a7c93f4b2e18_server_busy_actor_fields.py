"""server busy actor — busy_actor_type / busy_service_name

Revision ID: a7c93f4b2e18
Revises: 66ba803ba321
Create Date: 2026-09-08 11:20:00.000000

Раньше держателем брони мог быть только человек — `busy_user_id`. Бронь
«от имени сервиса» (ACS-снимки, а дальше testing_service) выражать было
нечем: сервис писал в `busy_user_id` id инициатора, и по карточке нельзя
было отличить «взял оператор» от «взял сервис по своей внутренней логике».

Две новые колонки:

  * `busy_actor_type` — `user` / `service`, NOT NULL с дефолтом `user`
    (все существующие брони — пользовательские);
  * `busy_service_name` — имя сервиса-держателя (`acs`, `testing_service`),
    заполняется только при `busy_actor_type='service'`.

`ck_servers_busy_actor` стережёт инвариант «либо человек, либо сервис»:
у `user` сервисного имени нет вовсе, у `service` обязано быть имя и пустой
`busy_user_id`. Существующий `ck_servers_busy_user_id_format` не трогаем —
он про формат идентификатора, а не про актора.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a7c93f4b2e18"
down_revision: Union[str, None] = "66ba803ba321"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_BUSY_ACTOR_CHECK = (
    "(busy_actor_type = 'user' AND busy_service_name IS NULL)"
    " OR (busy_actor_type = 'service'"
    " AND busy_service_name IS NOT NULL AND busy_user_id IS NULL)"
)


def upgrade() -> None:
    op.add_column(
        "servers",
        sa.Column(
            "busy_actor_type",
            sa.String(length=16),
            nullable=False,
            server_default="user",
        ),
    )
    op.add_column(
        "servers",
        sa.Column("busy_service_name", sa.String(length=64), nullable=True),
    )
    op.create_check_constraint(
        "ck_servers_busy_actor_type",
        "servers",
        "busy_actor_type IN ('user', 'service')",
    )
    op.create_check_constraint(
        "ck_servers_busy_actor",
        "servers",
        _BUSY_ACTOR_CHECK,
    )


def downgrade() -> None:
    op.drop_constraint("ck_servers_busy_actor", "servers", type_="check")
    op.drop_constraint("ck_servers_busy_actor_type", "servers", type_="check")
    op.drop_column("servers", "busy_service_name")
    op.drop_column("servers", "busy_actor_type")
