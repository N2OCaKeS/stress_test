"""split reinstall_status_action — worker_bot перевод с reinstall_start на reinstall_status_submit

Revision ID: f1234abc56e7
Revises: e9a7c2814d33
Create Date: 2026-05-21 15:00:00.000000

Раньше action `reinstall_start` использовался сразу для двух путей:

  * Пользовательский `POST /servers/{id}/reinstall` (dispatch PXE-пайплайна)
  * Worker callback `POST /internal/servers/{id}/reinstall_status` (репорт фазы)

worker_bot получал `(server, reinstall_start)` через `e9a7c2814d33`, и
технически тем же грантом мог бы дёрнуть пользовательский dispatch-эндпоинт.
Несмотря на смягчающие факторы (`subject_type=bot`, `allowed_services`
ограничен у PAT'а), это нарушение least-privilege.

Эта миграция:

  * заводит новый action `(server, reinstall_status_submit)` — отдельный
    под callback;
  * выдаёт его роли `admin` (по симметрии с baseline `_ALL_ACTIONS`);
  * меняет worker_bot грант: убирает `(server, reinstall_start)`,
    добавляет `(server, reinstall_status_submit)`. Числовой состав
    worker_bot остаётся 6 строк.

Endpoint `POST /internal/servers/{id}/reinstall_status` теперь чекает
`reinstall_status_submit`. Пользовательский `POST /servers/{id}/reinstall`
по-прежнему чекает `reinstall_start` — admin/operator не теряют доступ.
"""
from typing import Sequence, Union
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision: str = "f1234abc56e7"
down_revision: Union[str, None] = "e9a7c2814d33"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_NEW_GRANTS: list[tuple[str, str, str]] = [
    ("server", "admin", "reinstall_status_submit"),
    ("server", "worker_bot", "reinstall_status_submit"),
]


def upgrade() -> None:
    # 1. Снять старый worker_bot грант на reinstall_start (тот, что добавил
    #    `e9a7c2814d33`). Системный (department_id IS NULL).
    op.execute(
        "DELETE FROM entity_permissions "
        "WHERE role = 'worker_bot' "
        "  AND entity_type = 'server' "
        "  AND action = 'reinstall_start' "
        "  AND department_id IS NULL"
    )

    # 2. Завести новые грантовые строки. admin симметрично _ALL_ACTIONS из
    #    seed-миграции 831ba55543e9 — иначе пользователь с ролью `admin`
    #    в server_service не смог бы дёрнуть callback. worker_bot — основной
    #    потребитель.
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
            for entity_type, role, action in _NEW_GRANTS
        ],
    )


def downgrade() -> None:
    # 1. Снять admin+worker_bot гранты на reinstall_status_submit.
    op.execute(
        "DELETE FROM entity_permissions "
        "WHERE entity_type = 'server' "
        "  AND action = 'reinstall_status_submit' "
        "  AND role IN ('admin', 'worker_bot')"
    )
    # 2. Вернуть worker_bot грант на reinstall_start (восстановить состояние
    #    после `e9a7c2814d33` upgrade).
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
                "role": "worker_bot",
                "action": "reinstall_start",
            }
        ],
    )
