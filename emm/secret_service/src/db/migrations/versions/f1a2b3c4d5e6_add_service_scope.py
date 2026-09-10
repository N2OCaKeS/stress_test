"""add 'service' value to credential_scope

Revision ID: f1a2b3c4d5e6
Revises: e8c1a2d5f930
Create Date: 2026-09-07 00:00:00.000000

Новый scope `service` — та же форма владения, что `department`/
`cross_department` (`owner_dept_id IS NOT NULL`, `owner_user_id IS NULL`):
креда физически принадлежит отделу, который её завёл, и управляется
им же (dep_admin / admin secret_service своего dep'а — см.
`credential_service._can_manage_dept_scoped`). Отличие только в READ/REVEAL:
`access_service._check_service` даёт универсальный read/reveal ЛЮБОМУ
платформенному сервис-боту (`auth.BotAccount.is_service_bot=True`) поверх
обычной department-видимости — без ручных `DeptGrant` на каждую пару
отделов. Нужен для `testing_service`, который пушит статусы тестов в
Zephyr/Confluence от имени отдела, креды которого лежат в secret_service.

`scope` — native Postgres ENUM (`credential_scope`), поэтому новое значение
добавляется через `ALTER TYPE ... ADD VALUE`. Alembic по умолчанию гоняет все
миграции одного запуска в одной транзакции — использование свежедобавленного
enum-значения (например, в CHECK-constraint'е) в ТОЙ ЖЕ транзакции упадёт с
`unsafe use of new value`. `autocommit_block()` коммитит текущую транзакцию,
исполняет `ALTER TYPE` в autocommit-режиме и открывает новую — дальше по
миграции 'service' уже закоммичен и безопасен к использованию.
"""
from typing import Sequence, Union

from alembic import op


revision: str = "f1a2b3c4d5e6"
down_revision: Union[str, None] = "e8c1a2d5f930"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE credential_scope ADD VALUE IF NOT EXISTS 'service'")

    op.drop_constraint("ck_credentials_dept_owner", "credentials", type_="check")
    op.create_check_constraint(
        "ck_credentials_dept_owner",
        "credentials",
        "(scope NOT IN ('department', 'cross_department', 'service')) OR "
        "(owner_dept_id IS NOT NULL AND owner_user_id IS NULL)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_credentials_dept_owner", "credentials", type_="check")
    op.create_check_constraint(
        "ck_credentials_dept_owner",
        "credentials",
        "(scope NOT IN ('department', 'cross_department')) OR "
        "(owner_dept_id IS NOT NULL AND owner_user_id IS NULL)",
    )
    # Postgres не даёт удалить значение из enum без пересоздания типа —
    # 'service' остаётся в `credential_scope` даже после отката CHECK'а.
    # Безопасно: constraint снова запрещает такие строки, новых не появится.
