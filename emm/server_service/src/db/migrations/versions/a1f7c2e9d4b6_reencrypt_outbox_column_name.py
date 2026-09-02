"""reencrypt outbox column_name

Revision ID: a1f7c2e9d4b6
Revises: c7f1a4d9e2b3
Create Date: 2026-07-02 12:00:00.000000

Раньше outbox покрывал ровно два `password_encrypted` поля (server_accounts и
ipmi_controllers), и уникальность «одна активная задача на owner-row» держал
partial-индекс `(entity_type, entity_id)`. Теперь перешифровка идёт по всем
шифр-колонкам всех таблиц (mgmt-пароль/ключ сервера, previous_*, ssh-ключ
аккаунта), а у одной owner-row таких полей несколько — их задачи обязаны
различаться по имени колонки.

Добавляем `column_name`, бэкфилим существующие row'ы значением
`password_encrypted` (единственная колонка, что покрывалась раньше), делаем
NOT NULL и пересобираем partial-unique на тройку
`(entity_type, entity_id, column_name)`.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a1f7c2e9d4b6"
down_revision: Union[str, None] = "c7f1a4d9e2b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "secrets_reencrypt_outbox",
        sa.Column(
            "column_name",
            sa.String(length=64),
            nullable=False,
            server_default="password_encrypted",
        ),
    )
    # Старый unique покрывал (entity_type, entity_id); заменяем на тройку,
    # чтобы разные шифр-колонки одной owner-row не конфликтовали.
    op.drop_index(
        "uq_secrets_reencrypt_outbox_entity_active",
        table_name="secrets_reencrypt_outbox",
    )
    op.create_index(
        "uq_secrets_reencrypt_outbox_entity_active",
        "secrets_reencrypt_outbox",
        ["entity_type", "entity_id", "column_name"],
        unique=True,
        postgresql_where=sa.text("status IN ('pending', 'processing')"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_secrets_reencrypt_outbox_entity_active",
        table_name="secrets_reencrypt_outbox",
    )
    op.create_index(
        "uq_secrets_reencrypt_outbox_entity_active",
        "secrets_reencrypt_outbox",
        ["entity_type", "entity_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('pending', 'processing')"),
    )
    op.drop_column("secrets_reencrypt_outbox", "column_name")
