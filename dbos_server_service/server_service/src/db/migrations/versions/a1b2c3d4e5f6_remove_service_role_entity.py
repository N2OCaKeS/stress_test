"""remove service_role entity from server_service permission matrix

Revision ID: a1b2c3d4e5f6
Revises: f1234abc56e7
Create Date: 2026-05-22 06:00:00.000000

Управление service-ролями (создание/удаление имён ролей) переносится в
``auth_service`` целиком — ``server_service`` больше не проксирует
``GET/POST /roles`` и ``DELETE /roles/{name}``. Самоуправление матрицей
``entity_permissions`` остаётся, но привязывается теперь к новой сущности
``permission`` (вместо ``service_role``).

На свежих БД seed-миграция ``831ba55543e9`` уже сидит rows под
``permission``-entity. Эта миграция нужна для уже развернутых сред, в
которых seed-миграция была применена до правки и оставила ``service_role``
rows. Idempotent: повторный DELETE на пустой таблице — noop.

Никаких таблиц или FK не дропаем — реестр самих ролей живёт в
``auth_service``, в БД ``server_service`` он никогда не материализовался.
"""
from typing import Sequence, Union
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "f1234abc56e7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Снести любые service_role строки — system-wide seed legacy-варианта
    # `831ba55543e9` + кастомные per-department gранты, что могли быть
    # выписаны через старый /permissions endpoint до выкатки.
    op.execute(
        "DELETE FROM entity_permissions WHERE entity_type = 'service_role'"
    )

    # Засеять permission-entity, если её ещё нет. На свежей БД `831ba55543e9`
    # после правки сидит эти rows сам — UNIQUE-индекс отбьёт дубль через
    # ON CONFLICT-эмуляцию: вставляем только то, чего ещё нет.
    bind = op.get_bind()
    existing = {
        (row.role, row.action)
        for row in bind.execute(
            sa.text(
                "SELECT role, action FROM entity_permissions "
                "WHERE entity_type = 'permission' AND department_id IS NULL"
            )
        )
    }
    seed_rows = [
        ("admin", "view"),
        ("admin", "permission_grant"),
        ("admin", "permission_revoke"),
        ("reader", "view"),
        ("operator", "view"),
    ]
    missing = [(role, action) for role, action in seed_rows if (role, action) not in existing]
    if missing:
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
                    "entity_type": "permission",
                    "role": role,
                    "action": action,
                }
                for role, action in missing
            ],
        )


def downgrade() -> None:
    # Снести permission-сидинг и восстановить service_role rows (зеркало
    # прежнего baseline). Operator получал только `view`, reader — тоже
    # только `view`, admin — все 5 actions.
    op.execute(
        "DELETE FROM entity_permissions WHERE entity_type = 'permission'"
    )
    legacy = [
        ("service_role", "admin", "view"),
        ("service_role", "admin", "role_create"),
        ("service_role", "admin", "role_delete"),
        ("service_role", "admin", "permission_grant"),
        ("service_role", "admin", "permission_revoke"),
        ("service_role", "reader", "view"),
        ("service_role", "operator", "view"),
    ]
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
            for entity_type, role, action in legacy
        ],
    )
