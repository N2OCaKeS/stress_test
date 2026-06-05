"""db sweep: CHECK constraints, identity-format guards, index hygiene

Revision ID: f8e9d0c1b2a3
Revises: e7a4d951c3b2
Create Date: 2026-06-05 04:09:00.000000

Bundle defense-in-depth fixes по persistence-слою server_service:

1. Длина envelope'а зашифрованных секретов. `password_encrypted` и
   `ssh_private_key_encrypted` — TEXT, верхней границы нет. Реальный
   envelope `v<key>$<nonce>$<ciphertext+tag>` для AES-256-GCM на пароль
   до 256 байт plaintext выходит ~400 символов, для приватного SSH-ключа
   (Ed25519/RSA-4096) — до ~3-4 КБ. Лимит 8192 даёт двукратный запас и
   защищает от tooling-bug, который мог бы тихо аккумулировать строку до
   TOAST-OOM.

2. Enum-as-varchar CHECK'и. Колонки `secrets_reencrypt_outbox.status` и
   `server_accounts.source` хранят строки из фиксированного множества; на
   эти строки опираются partial-индексы и бизнес-логика. Опечатка в коде
   тихо просочится в БД и выпадет из poller'ов. CHECK закрывает кейс.

3. Soft-FK identity format. `servers.created_by`, `servers.busy_user_id`,
   `server_accounts.created_by`, `server_accounts.linked_user_id`,
   `entity_permissions.granted_by` — это `String(64)` без FK на
   auth_service (две отдельные БД). Формат `usr_<hex>` или `bot_<hex>`
   держится только app-кодом; CHECK по regex закрывает surface для
   cross-сервисного bug'а, который мог бы записать произвольную строку.

4. Index hygiene. `ix_entity_permissions_department_id` дублируется
   partial unique `uq_entity_permissions_per_dept` для write-uniqueness;
   read-pattern идёт через composite. `ix_dispatch_outbox_task_id`
   `repositories/dispatch_outbox.py` никогда не query'ит — только INSERT.
   Оба индекса можно снять.

5. `alembic alter_column server_default=None` без `existing_*` keywords
   в `a3f1b8c2d495` — здесь не правится (миграция уже применена в проде),
   но колонкам `server_accounts.source` и
   `server_account_servers.present_on_server` фактический `server_default`
   уже `NULL`. Лечится в следующей миграции при необходимости.

Скоупом миграции **не** покрыты:

* cross-table инвариант `server_accounts.department_id` ↔
  `server_account_servers.server_id` (требует trigger function);
* GIN на `dispatch_outbox.payload` (payload не query'ится по содержимому
  by design — см. `repositories/dispatch_outbox.py`);
* drop `servers.os_last_synced_at` — колонка живая
  (`services/server.py:873`, `services/internal_service.py:831`).
"""
from typing import Sequence, Union

from alembic import op

revision: str = "f8e9d0c1b2a3"
down_revision: Union[str, None] = "e7a4d951c3b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Regex для soft-FK на auth_service.users / bot_accounts. Формат генерится
# `auth_service/src/utils/ids.py` (`usr_<32-hex>`, `bot_<32-hex>`). Закрываем
# обе ветки одним выражением, длина не фиксирована (форвард-совместимо).
_IDENTITY_REGEX = r"^(usr_|bot_)[A-Za-z0-9_-]+$"


def upgrade() -> None:
    # 1. Length-cap на зашифрованные envelope'ы.
    op.create_check_constraint(
        "ck_server_accounts_password_encrypted_len",
        "server_accounts",
        "password_encrypted IS NULL OR length(password_encrypted) < 8192",
    )
    op.create_check_constraint(
        "ck_server_accounts_ssh_private_key_len",
        "server_accounts",
        "ssh_private_key_encrypted IS NULL OR length(ssh_private_key_encrypted) < 8192",
    )
    op.create_check_constraint(
        "ck_ipmi_controllers_password_encrypted_len",
        "ipmi_controllers",
        "length(password_encrypted) < 8192",
    )

    # 2. Enum-as-varchar CHECK'и.
    op.create_check_constraint(
        "ck_secrets_reencrypt_outbox_status",
        "secrets_reencrypt_outbox",
        "status IN ('pending', 'processing', 'done', 'failed')",
    )
    op.create_check_constraint(
        "ck_server_accounts_source",
        "server_accounts",
        "source IN ('managed', 'discovered')",
    )

    # 3. Identity-format CHECK'и на soft-FK колонки. Везде NULL разрешён —
    #    legacy-строки и system-actor могли писать NULL.
    op.create_check_constraint(
        "ck_servers_created_by_format",
        "servers",
        f"created_by IS NULL OR created_by ~ '{_IDENTITY_REGEX}'",
    )
    op.create_check_constraint(
        "ck_servers_busy_user_id_format",
        "servers",
        f"busy_user_id IS NULL OR busy_user_id ~ '{_IDENTITY_REGEX}'",
    )
    op.create_check_constraint(
        "ck_server_accounts_created_by_format",
        "server_accounts",
        f"created_by IS NULL OR created_by ~ '{_IDENTITY_REGEX}'",
    )
    op.create_check_constraint(
        "ck_server_accounts_linked_user_id_format",
        "server_accounts",
        f"linked_user_id IS NULL OR linked_user_id ~ '{_IDENTITY_REGEX}'",
    )
    op.create_check_constraint(
        "ck_entity_permissions_granted_by_format",
        "entity_permissions",
        f"granted_by IS NULL OR granted_by ~ '{_IDENTITY_REGEX}'",
    )

    # 4. Index hygiene — drop'аем дубль и неиспользуемый.
    op.drop_index(
        "ix_entity_permissions_department_id", table_name="entity_permissions"
    )
    op.drop_index("ix_dispatch_outbox_task_id", table_name="dispatch_outbox")


def downgrade() -> None:
    # Восстанавливаем индексы.
    op.create_index(
        "ix_dispatch_outbox_task_id",
        "dispatch_outbox",
        ["task_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_entity_permissions_department_id"),
        "entity_permissions",
        ["department_id"],
        unique=False,
    )

    # Снимаем CHECK'и в обратном порядке.
    op.drop_constraint(
        "ck_entity_permissions_granted_by_format",
        "entity_permissions",
        type_="check",
    )
    op.drop_constraint(
        "ck_server_accounts_linked_user_id_format",
        "server_accounts",
        type_="check",
    )
    op.drop_constraint(
        "ck_server_accounts_created_by_format",
        "server_accounts",
        type_="check",
    )
    op.drop_constraint(
        "ck_servers_busy_user_id_format", "servers", type_="check"
    )
    op.drop_constraint(
        "ck_servers_created_by_format", "servers", type_="check"
    )

    op.drop_constraint(
        "ck_server_accounts_source", "server_accounts", type_="check"
    )
    op.drop_constraint(
        "ck_secrets_reencrypt_outbox_status",
        "secrets_reencrypt_outbox",
        type_="check",
    )

    op.drop_constraint(
        "ck_ipmi_controllers_password_encrypted_len",
        "ipmi_controllers",
        type_="check",
    )
    op.drop_constraint(
        "ck_server_accounts_ssh_private_key_len",
        "server_accounts",
        type_="check",
    )
    op.drop_constraint(
        "ck_server_accounts_password_encrypted_len",
        "server_accounts",
        type_="check",
    )
