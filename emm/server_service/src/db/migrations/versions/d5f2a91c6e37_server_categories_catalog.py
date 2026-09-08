"""server_categories catalog + servers.category_id + entity permissions

Revision ID: d5f2a91c6e37
Revises: a7c93f4b2e18
Create Date: 2026-09-08 13:10:00.000000

Каталог категорий серверов по мощности — управляемый данными, а не enum'ом в
коде: стартовые четыре записи сидятся здесь, дальше категории заводятся через
`POST /server-categories`.

`servers.category_id` — nullable FK с ondelete=RESTRICT: уже заведённые
серверы категории не получают (простановка ручная), а удалить категорию,
на которую кто-то ссылается, нельзя.

Права зоны `server_category` дублируются строкой (зеркало
`constants.ENTITY_ACTIONS[SERVER_CATEGORY]`), чтобы миграция была
самодостаточной: чтение каталога открыто любому аутентифицированному актору,
поэтому `view` в матрице нет — только запись у системной роли `admin`.
"""
from typing import Sequence, Union
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision: str = "d5f2a91c6e37"
down_revision: Union[str, None] = "a7c93f4b2e18"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_CATEGORY_ACTIONS: list[str] = ["create", "update", "delete"]

# Стартовый набор: названия из allta_app, под которые уже расписаны стенды.
_SEED_CATEGORIES: list[tuple[str, str, str]] = [
    ("low_server", "LowServer", "Сервер начального уровня по CPU/RAM."),
    ("middle_server", "MiddleServer", "Сервер среднего уровня по CPU/RAM."),
    ("high_server", "HighServer", "Сервер высокого уровня по CPU/RAM."),
    ("workstation", "WorkStation", "Рабочая станция, не серверное железо."),
]


def upgrade() -> None:
    op.create_table(
        "server_categories",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("label", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.Column("created_by", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "ix_server_categories_code", "server_categories", ["code"], unique=True,
    )

    categories = sa.table(
        "server_categories",
        sa.column("id", sa.String),
        sa.column("code", sa.String),
        sa.column("label", sa.String),
        sa.column("description", sa.Text),
    )
    op.bulk_insert(
        categories,
        [
            {
                "id": f"scat_{code}",
                "code": code,
                "label": label,
                "description": description,
            }
            for code, label, description in _SEED_CATEGORIES
        ],
    )

    op.add_column(
        "servers", sa.Column("category_id", sa.String(length=64), nullable=True),
    )
    op.create_index("ix_servers_category_id", "servers", ["category_id"])
    op.create_foreign_key(
        "fk_servers_category_id",
        "servers",
        "server_categories",
        ["category_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    permissions = sa.table(
        "entity_permissions",
        sa.column("id", sa.String),
        sa.column("entity_type", sa.String),
        sa.column("role", sa.String),
        sa.column("action", sa.String),
    )
    op.bulk_insert(
        permissions,
        [
            {
                "id": f"prm_{uuid4().hex}",
                "entity_type": "server_category",
                "role": "admin",
                "action": action,
            }
            for action in _CATEGORY_ACTIONS
        ],
    )


def downgrade() -> None:
    op.execute("DELETE FROM entity_permissions WHERE entity_type = 'server_category'")
    op.drop_constraint("fk_servers_category_id", "servers", type_="foreignkey")
    op.drop_index("ix_servers_category_id", table_name="servers")
    op.drop_column("servers", "category_id")
    op.drop_index("ix_server_categories_code", table_name="server_categories")
    op.drop_table("server_categories")
