"""ipmi reveal-credentials + drop boot_order/pxe/reinstall grants

Revision ID: b8d4e3f9a712
Revises: a2c1d8e4b9f5
Create Date: 2026-05-22 07:00:00.000000

Меняет три семейства грантов в `entity_permissions`:

1. **Добавляет** `(ipmi_controller, reveal_credentials)` для `admin` и
   `operator` — новый user-facing action для расшифровки IPMI-логина/пароля
   через `POST /api/server/v1/ipmi-controllers/{controller_id}/reveal-credentials`.
   Симметричен с `(server_account, reveal_password)` из `a2c1d8e4b9f5`.
   reader/guest не получают reveal-доступа, worker_bot — тоже (для
   worker'а есть отдельный internal endpoint `view_credentials`).

2. **Удаляет** все гранты на boot order: `(server, boot_order_view)`,
   `(server, boot_order_set)`. Endpoint'ы `GET/POST /servers/{id}/ipmi/boot-order`
   и worker-task'и `boot.order_get`/`boot.order_set` сняты — гранты
   осиротели.

3. **Удаляет** все гранты на PXE/reinstall: `(server, pxe_boot)`,
   `(server, reinstall_start)`, `(server, reinstall_status_submit)`.
   Endpoint'ы `POST /servers/{id}/reinstall`, callback'и `/internal/.../
   reinstall_status` и worker-task'и `reinstall.start`/`boot.pxe_once`
   сняты, action'ы тоже убраны из `core/constants.py::ENTITY_ACTIONS`.

Downgrade воссоздаёт гранты по тому же seed-составу, что был в
`831ba55543e9_seed_default_entity_permissions.py` + `e9a7c2814d33` +
`f1234abc56e7` (для symmetry, чтобы alembic downgrade хождение туда-обратно
не било матрицу).
"""
from typing import Sequence, Union
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision: str = "b8d4e3f9a712"
down_revision: Union[str, None] = "a2c1d8e4b9f5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Новые гранты на reveal IPMI credentials.
_NEW_GRANTS: list[tuple[str, str, str]] = [
    ("ipmi_controller", "admin", "reveal_credentials"),
    ("ipmi_controller", "operator", "reveal_credentials"),
]

# Удаляемые гранты — все упоминания boot_order / pxe / reinstall.
# Подметаем по action-name'у в один DELETE, чтобы не зависеть от того,
# кому именно эти права когда-то выдавались (admin / operator / worker_bot
# / custom-роль).
_DROPPED_ACTIONS_SERVER: list[str] = [
    "boot_order_view",
    "boot_order_set",
    "pxe_boot",
    "reinstall_start",
    "reinstall_status_submit",
]


def upgrade() -> None:
    # 1. Удалить осиротевшие гранты — независимо от роли и department_id.
    actions_csv = ", ".join(f"'{a}'" for a in _DROPPED_ACTIONS_SERVER)
    op.execute(
        f"DELETE FROM entity_permissions "
        f"WHERE entity_type = 'server' AND action IN ({actions_csv})"
    )

    # 2. Засеять новые reveal-гранты. UNIQUE-индекс на
    # (entity_type, role, action, department_id IS NULL) уже есть в схеме —
    # повторный alembic upgrade не уронит (фильтр существующих).
    bind = op.get_bind()
    existing = {
        (row.role, row.action)
        for row in bind.execute(
            sa.text(
                "SELECT role, action FROM entity_permissions "
                "WHERE entity_type = 'ipmi_controller' "
                "  AND action = 'reveal_credentials' "
                "  AND department_id IS NULL"
            )
        )
    }
    missing = [
        (entity, role, action)
        for entity, role, action in _NEW_GRANTS
        if (role, action) not in existing
    ]
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
                    "entity_type": entity_type,
                    "role": role,
                    "action": action,
                }
                for entity_type, role, action in missing
            ],
        )


def downgrade() -> None:
    # 1. Снять reveal_credentials гранты.
    op.execute(
        "DELETE FROM entity_permissions "
        "WHERE entity_type = 'ipmi_controller' "
        "  AND action = 'reveal_credentials' "
        "  AND role IN ('admin', 'operator')"
    )
    # 2. Восстановить boot/pxe/reinstall гранты по составу из
    # `831ba55543e9` + `e9a7c2814d33` + `f1234abc56e7`.
    restored: list[tuple[str, str]] = [
        # admin — все 5 actions.
        ("admin", "boot_order_view"),
        ("admin", "boot_order_set"),
        ("admin", "pxe_boot"),
        ("admin", "reinstall_start"),
        ("admin", "reinstall_status_submit"),
        # operator — 4 (без reinstall_status_submit; см. seed).
        ("operator", "boot_order_view"),
        ("operator", "boot_order_set"),
        ("operator", "pxe_boot"),
        ("operator", "reinstall_start"),
        # worker_bot — только callback reinstall_status_submit (см.
        # f1234abc56e7); reader/guest — никаких.
        ("worker_bot", "reinstall_status_submit"),
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
                "entity_type": "server",
                "role": role,
                "action": action,
            }
            for role, action in restored
        ],
    )
