"""drop cpu_models table; add inline CPU fields to servers

Revision ID: b6f3a91d27e8
Revises: a4b7e1c92d05
Create Date: 2026-05-22 06:00:01.000000

Отдельная таблица-каталог `cpu_models` отменяется. CPU-данные хранятся
плоско в строке `servers`:

  * `cpu_brand` (Intel/AMD/MCST/...) — String(64).
  * `cpu_model` (Xeon Silver 4314) — String(256).
  * `cpu_cores` — Integer.
  * `cpu_threads` — Integer.
  * `cpu_frequency_ghz` — Float.

Обновляются inventory probe'ом или вручную через `PATCH /servers/{id}`.

Что делает миграция:

  1. drop FK `servers.cpu_id` → `cpu_models.id` + index `ix_servers_cpu_id`.
  2. drop colums `servers.cpu_id`, `servers.cpu_count`.
  3. add colums `servers.cpu_brand`, `servers.cpu_model`, `servers.cpu_cores`,
     `servers.cpu_threads`, `servers.cpu_frequency_ghz`.
  4. drop table `cpu_models` + её indexes.
  5. чистим `entity_permissions` от записей с `entity_type='cpu_model'`
     (admin/reader/operator грантов из seed-миграции `831ba55543e9`).

Downgrade полностью воспроизводит обратное: возвращает таблицу `cpu_models`
с тем же составом колонок, возвращает FK и колонки `cpu_id`/`cpu_count` в
`servers`, восстанавливает seed cpu_model-гранты (admin: view/create/update/
delete; reader/operator: view).
"""
from typing import Sequence, Union
from uuid import uuid4

import sqlalchemy as sa
from alembic import op


revision: str = "b6f3a91d27e8"
down_revision: Union[str, None] = "a4b7e1c92d05"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Снять FK + индекс с servers.cpu_id (имя FK генерится alembic'ом
    #    autodetect'ом, для надёжности используем явное составное имя из
    #    initial-миграции).
    op.drop_index("ix_servers_cpu_id", table_name="servers")
    with op.batch_alter_table("servers") as batch:
        batch.drop_constraint("servers_cpu_id_fkey", type_="foreignkey")
        batch.drop_column("cpu_id")
        batch.drop_column("cpu_count")
        batch.add_column(sa.Column("cpu_brand", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("cpu_model", sa.String(length=256), nullable=True))
        batch.add_column(sa.Column("cpu_cores", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("cpu_threads", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("cpu_frequency_ghz", sa.Float(), nullable=True))

    # 2. Сам каталог cpu_models больше не нужен.
    op.drop_index("ix_cpu_models_architecture", table_name="cpu_models")
    op.drop_table("cpu_models")

    # 3. Снять все entity_permissions для удалённой сущности. Это покрывает
    #    seed-гранты (831ba55543e9): admin полный набор + reader/operator view.
    op.execute("DELETE FROM entity_permissions WHERE entity_type = 'cpu_model'")


def downgrade() -> None:
    # WARNING: downgrade теряет данные. Таблица `cpu_models` и
    # `servers.cpu_id` восстанавливаются пустыми (cpu_id = NULL у всех
    # серверов); inline-поля `cpu_brand`/`cpu_model`/`cpu_cores`/`cpu_threads`/
    # `cpu_frequency_ghz` дропаются безвозвратно. Для prod-отката оператор
    # обязан сначала выгрузить inline-поля через COPY.
    # 1. Восстановить таблицу cpu_models в том же виде, что initial-миграция.
    op.create_table(
        "cpu_models",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("vendor", sa.String(length=64), nullable=True),
        sa.Column("architecture", sa.String(length=32), nullable=False),
        sa.Column("cores", sa.Integer(), nullable=False),
        sa.Column("threads", sa.Integer(), nullable=False),
        sa.Column("base_frequency_mhz", sa.Integer(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "discovered_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_index(
        "ix_cpu_models_architecture", "cpu_models", ["architecture"], unique=False,
    )

    # 2. Вернуть в servers колонки cpu_id / cpu_count и снять inline-поля.
    with op.batch_alter_table("servers") as batch:
        batch.drop_column("cpu_frequency_ghz")
        batch.drop_column("cpu_threads")
        batch.drop_column("cpu_cores")
        batch.drop_column("cpu_model")
        batch.drop_column("cpu_brand")
        batch.add_column(sa.Column("cpu_id", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("cpu_count", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "servers_cpu_id_fkey", "cpu_models", ["cpu_id"], ["id"],
            ondelete="RESTRICT",
        )
    op.create_index("ix_servers_cpu_id", "servers", ["cpu_id"], unique=False)

    # 3. Восстановить seed-гранты cpu_model из 831ba55543e9: admin полный набор,
    #    reader/operator только view.
    grants: list[tuple[str, str, str]] = []
    for action in ("view", "create", "update", "delete"):
        grants.append(("cpu_model", "admin", action))
    grants.append(("cpu_model", "reader", "view"))
    grants.append(("cpu_model", "operator", "view"))
    table = sa.table(
        "entity_permissions",
        sa.column("id", sa.String),
        sa.column("entity_type", sa.String),
        sa.column("role", sa.String),
        sa.column("action", sa.String),
    )
    op.bulk_insert(
        table,
        [
            {
                "id": f"prm_{uuid4().hex}",
                "entity_type": entity_type,
                "role": role,
                "action": action,
            }
            for entity_type, role, action in grants
        ],
    )
