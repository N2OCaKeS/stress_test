"""seed (server_account, adopt_from_host) grant for admin / operator

Адресует endpoint `POST /server-accounts/{id}/adopt_from_host` — оператор
принимает факт-состояние OS-пользователя с конкретного хоста в БД (пополевно,
по drift'у инвентаризации). Action добавлен в
`constants.ENTITY_ACTIONS[server_account]`; этот seed подкатывает дефолтный
grant ровно как `e3f8c4b21a07` для `view_drift`.

* admin — полный доступ (как ко всем server_account-action'ам).
* operator — получает: adopt — это update-уровень (правит БД-атрибуты
  аккаунта), оператор и так держит `update`/`create` на server_account.
* reader — НЕ получает: инвариант «reader только view» сохраняется.
* worker_bot — не получает: adopt инициируется человеком, не воркером.

Revision ID: a1c5e9f3b7d2
Revises: d6c1f8a3b9e4
Create Date: 2026-06-17
"""

from typing import Sequence, Union
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision: str = "a1c5e9f3b7d2"
down_revision: Union[str, None] = "d6c1f8a3b9e4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_GRANTS: list[tuple[str, str, str]] = [
    ("server_account", "admin", "adopt_from_host"),
    ("server_account", "operator", "adopt_from_host"),
]


def upgrade() -> None:
    # bulk-insert с явным id (prm_<uuid4>), единый стиль с
    # `831ba55543e9_seed_default_entity_permissions`. NOT EXISTS защищает от
    # unique-violation на partial global index при re-apply.
    for entity_type, role, action in _GRANTS:
        op.execute(
            sa.text(
                """
                INSERT INTO entity_permissions (id, entity_type, role, action, department_id)
                SELECT :id, :etype, :role, :action, NULL
                WHERE NOT EXISTS (
                    SELECT 1 FROM entity_permissions
                    WHERE entity_type = :etype
                      AND role = :role
                      AND action = :action
                      AND department_id IS NULL
                )
                """
            ).bindparams(
                id=f"prm_{uuid4().hex}",
                etype=entity_type,
                role=role,
                action=action,
            )
        )


def downgrade() -> None:
    op.execute(
        "DELETE FROM entity_permissions "
        "WHERE entity_type = 'server_account' AND action = 'adopt_from_host'"
    )
