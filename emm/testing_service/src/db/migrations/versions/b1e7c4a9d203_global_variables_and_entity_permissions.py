"""global_variables catalog + entity_permissions matrix

Revision ID: b1e7c4a9d203
Revises:
Create Date: 2026-09-09 12:40:00.000000

Первый домен testing_service с реальными данными — вместе с ним заводится и
ролевая инфраструктура: `entity_permissions` повторяет структуру одноимённой
таблицы в server_service/secret_service (system-wide строка при
`department_id IS NULL`, per-department при заполненном).

Каталог глобальных переменных сидится обязательным набором из плана
миграции (§2.1). `choices_source` у `MODE` — фиксированное множество режимов
безопасности Astra; у `RC`/`KERNEL` — именованные резолверы, спрашивающие
живой каталог у server_service.

Права зоны `global_variable` дублируются строкой (зеркало
`constants.ENTITY_ACTIONS`), чтобы миграция была самодостаточной. `view`
сеется вместе с записью на будущее: сегодня чтение каталога открыто любому
аутентифицированному актору и матрицу не спрашивает.
"""
from typing import Sequence, Union
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision: str = "b1e7c4a9d203"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_GLOBAL_VARIABLE_ACTIONS: list[str] = ["view", "create", "update", "delete"]

# (code, label, source, value_type, choices_source, is_sensitive, description)
_SEED_VARIABLES: list[tuple[str, str, str, str, str | None, bool, str]] = [
    (
        "RC", "РЦ (версия ОС)", "launch_context", "string",
        "dynamic:os_versions", False,
        "Версия ОС, на которой гоняется прогон. Список берётся из каталога server_service.",
    ),
    (
        "STAND", "Стенд", "launch_context", "string",
        None, False,
        "Стенд, на котором исполняется тест. Резолвер появится вместе с каталогом стендов.",
    ),
    (
        "KERNEL", "Ядро", "launch_context", "string",
        "dynamic:kernels", False,
        "Версия ядра. Список зависит от версии ОС — резолверу нужен os_version_id.",
    ),
    (
        "MODE", "Режим безопасности Astra", "launch_context", "string",
        'static:["orel","smolensk"]', False,
        "Уровень защищённости Astra SE, выставляется пайплайном prepare-for-test.",
    ),
    (
        "TESTENV", "Тестовое окружение", "launch_context", "string",
        None, False,
        "Окружение прогона (легаси-флаг -te).",
    ),
    (
        "HOME_DIR", "Домашний каталог тестового пользователя", "launch_context", "string",
        None, False,
        "База для путей внутри тестов, /home/<TEST_USER>.",
    ),
    (
        "TEST_USER", "Пользователь исполнения теста", "launch_context", "string",
        None, False,
        "Имя пользователя, под которым тест запускается по SSH. Настраивается per-department.",
    ),
    (
        "TEST_PASSWORD", "Пароль тестового пользователя", "launch_context", "string",
        None, True,
        "Пароль учётки исполнения теста. В логах прогона маскируется.",
    ),
    (
        "TEST_SSH_KEY", "SSH-ключ тестового пользователя", "launch_context", "string",
        None, True,
        "Приватный ключ учётки исполнения теста. В логах прогона маскируется.",
    ),
]


def upgrade() -> None:
    op.create_table(
        "entity_permissions",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("entity_type", sa.String(length=64), nullable=False),
        sa.Column("role", sa.String(length=64), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("department_id", sa.String(length=64), nullable=True),
        sa.Column("granted_by", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
    )
    op.create_index(
        "ix_entity_permissions_entity_type", "entity_permissions", ["entity_type"],
    )
    op.create_index("ix_entity_permissions_role", "entity_permissions", ["role"])
    op.create_index(
        "uq_entity_permissions_global",
        "entity_permissions",
        ["entity_type", "role", "action"],
        unique=True,
        postgresql_where=sa.text("department_id IS NULL"),
    )
    op.create_index(
        "uq_entity_permissions_per_dept",
        "entity_permissions",
        ["entity_type", "role", "action", "department_id"],
        unique=True,
        postgresql_where=sa.text("department_id IS NOT NULL"),
    )

    op.create_table(
        "global_variables",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("label", sa.String(length=128), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("value_type", sa.String(length=32), nullable=False),
        sa.Column("choices_source", sa.String(length=512), nullable=True),
        sa.Column(
            "is_sensitive", sa.Boolean(), nullable=False, server_default=sa.text("false"),
        ),
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
        "ix_global_variables_code", "global_variables", ["code"], unique=True,
    )

    variables = sa.table(
        "global_variables",
        sa.column("id", sa.String),
        sa.column("code", sa.String),
        sa.column("label", sa.String),
        sa.column("source", sa.String),
        sa.column("value_type", sa.String),
        sa.column("choices_source", sa.String),
        sa.column("is_sensitive", sa.Boolean),
        sa.column("description", sa.Text),
    )
    op.bulk_insert(
        variables,
        [
            {
                "id": f"gvar_{code.lower()}",
                "code": code,
                "label": label,
                "source": source,
                "value_type": value_type,
                "choices_source": choices_source,
                "is_sensitive": is_sensitive,
                "description": description,
            }
            for code, label, source, value_type, choices_source, is_sensitive, description
            in _SEED_VARIABLES
        ],
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
                "entity_type": "global_variable",
                "role": "admin",
                "action": action,
            }
            for action in _GLOBAL_VARIABLE_ACTIONS
        ],
    )


def downgrade() -> None:
    op.drop_index("ix_global_variables_code", table_name="global_variables")
    op.drop_table("global_variables")
    op.drop_index("uq_entity_permissions_per_dept", table_name="entity_permissions")
    op.drop_index("uq_entity_permissions_global", table_name="entity_permissions")
    op.drop_index("ix_entity_permissions_role", table_name="entity_permissions")
    op.drop_index("ix_entity_permissions_entity_type", table_name="entity_permissions")
    op.drop_table("entity_permissions")
