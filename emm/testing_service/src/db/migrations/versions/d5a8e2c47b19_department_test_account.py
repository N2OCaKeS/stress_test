"""Department test account (credential link + home template)

Revision ID: d5a8e2c47b19
Revises: a4c9e2f17b35
Create Date: 2026-09-24 00:00:00.000000

«Тестовая учётка» отдела живёт в
secret_service (credential scope=service, service=test_account: login +
JSON с паролем и SSH-парой), в `department_test_settings` — только ссылка
`test_account_credential_id` и нечувствительный шаблон
домашнего каталога `test_account_home_template`.

Значения по умолчанию — легаси allta_app:

* логин `u` — `emm/allta_app_full/libs/liballta.py:113` (`user = 'u'`);
  уже лежит в `department_test_settings.test_username` (дефолт `u`) и
  остаётся там подсказкой: страница «Тестовая учётка» предлагает его, пока
  credential не заведён;
* домашний каталог `/home/u` — `emm/allta_app_full/starter.sh:54`
  (`cd /home/u/git`) и `emm/allta_app_full/libs/liballta.py:99`
  (`/home/u/url`), отсюда шаблон `/home/{TEST_USER}`;
* общий пароль `srv_pass` (`emm/allta_app_full/libs/liballta.py:114`) —
  секрет, миграцией не переносится: его вводит администратор на странице
  учётки. До этого запуск падает `TEST_ACCOUNT_NOT_CONFIGURED`.

Переменные: `TEST_USER` → `login`,
`TEST_PASSWORD` → `password` (sensitive), `HOME_DIR` → `home` — сид
`b1e7c4a9d203` описывал их как значения учётки исполнения теста, но брал из
`launch_context`. Переводятся, только если их не трогали руками
(`source='launch_context' AND source_ref IS NULL`). Новая `TEST_HOME` —
тот же `home` под именем из плана (D11).

Права: новая сущность `department_test_account` — системная роль `admin`,
действия `view`/`update` (тот же паттерн, что `department_test_settings`).
department_admin своего отдела проходит мимо матрицы
(`permissions.require_department_action`).
"""
from typing import Sequence, Union
from uuid import uuid4

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d5a8e2c47b19"
down_revision: Union[str, None] = "a4c9e2f17b35"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_ACTIONS: list[str] = ["view", "update"]


# (code, field) — переменные, которые берут значение из тестовой учётки.
_ACCOUNT_VARIABLES: tuple[tuple[str, str], ...] = (
    ("TEST_USER", "login"),
    ("TEST_PASSWORD", "password"),
    ("HOME_DIR", "home"),
)


def _jsonb(name: str) -> sa.BindParameter:
    return sa.bindparam(name, type_=postgresql.JSONB(none_as_null=True))


def upgrade() -> None:
    op.add_column(
        "department_test_settings",
        sa.Column("test_account_credential_id", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "department_test_settings",
        sa.Column(
            "test_account_home_template", sa.String(length=256),
            nullable=False, server_default="/home/{TEST_USER}",
        ),
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
                "entity_type": "department_test_account",
                "role": "admin",
                "action": action,
            }
            for action in _ACTIONS
        ],
    )

    bind = op.get_bind()
    switch = sa.text(
        "UPDATE global_variables SET source = 'test_account', source_ref = :ref, "
        "is_sensitive = is_sensitive OR :sensitive, updated_at = now() "
        "WHERE code = :code AND source = 'launch_context' AND source_ref IS NULL"
    ).bindparams(_jsonb("ref"))
    for code, field_name in _ACCOUNT_VARIABLES:
        bind.execute(switch, {"code": code, "ref": {"field": field_name}, "sensitive": field_name == "password"})
    bind.execute(
        sa.text(
            "INSERT INTO global_variables "
            "(id, code, label, source, value_type, choices_source, is_sensitive, description, source_ref) "
            "VALUES ('gvar_test_home', 'TEST_HOME', 'Домашний каталог тестовой учётки', 'test_account', "
            "'string', NULL, false, :description, :ref) ON CONFLICT (code) DO NOTHING"
        ).bindparams(_jsonb("ref")),
        {
            "ref": {"field": "home"},
            "description": (
                "Домашний каталог пользователя исполнения теста по шаблону тестовой учётки "
                "отдела (по умолчанию /home/{TEST_USER}; легаси /home/u, starter.sh:54)."
            ),
        },
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text(
        "DELETE FROM global_variables WHERE code = 'TEST_HOME' AND created_by IS NULL "
        "AND id NOT IN (SELECT variable_id FROM test_command_args WHERE variable_id IS NOT NULL)"
    ))
    revert = sa.text(
        "UPDATE global_variables SET source = 'launch_context', source_ref = NULL, updated_at = now() "
        "WHERE code = :code AND source = 'test_account' AND source_ref = :ref"
    ).bindparams(_jsonb("ref"))
    for code, field_name in _ACCOUNT_VARIABLES:
        bind.execute(revert, {"code": code, "ref": {"field": field_name}})
    op.execute(
        sa.text("DELETE FROM entity_permissions WHERE entity_type = 'department_test_account'")
    )
    op.drop_column("department_test_settings", "test_account_home_template")
    op.drop_column("department_test_settings", "test_account_credential_id")
