"""tasks: cancellation fields (cancelled_by / cancelled_at / cancel_reason)

Revision ID: 0005_tasks_cancel_fields
Revises: 0004_outbox_next_retry_at
Create Date: 2026-05-30 00:00:00.000000

Поднимает реальный cancel у task'и. До сих пор `TaskStatus.CANCELLED` был
зарезервированным членом enum'а без эмиттера: ни server_service, ни worker
никогда его не выставляли. F22-C добавляет endpoint
`POST /api/server/v1/tasks/{id}/cancel` — он UPDATE'ит row в этой таблице
со стороны server_service через cross-DB engine, выставляя status и
аудит-поля.

Поля:

* `cancelled_by` — user_id оператора, который дёрнул cancel. Аналогично
  `created_by` — в task-таблице нет внешних ключей к auth_service.
* `cancelled_at` — UTC-метка решения отменить (момент UPDATE'а, не момент
  фактического graceful-завершения running task'и).
* `cancel_reason` — свободный текст причины из тела POST'а, обрезается на
  стороне server_service. На уровне DB лимит 512, чтобы влезало в
  стандартный VARCHAR без TEXT.

Колонки nullable: legacy-row до миграции остаются без них, а CANCELLED
для штатных succeeded/failed строк нерелевантен.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005_tasks_cancel_fields"
down_revision: Union[str, None] = "0004_outbox_next_retry_at"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column("cancelled_by", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "tasks",
        sa.Column(
            "cancelled_at", sa.DateTime(timezone=True), nullable=True,
        ),
    )
    op.add_column(
        "tasks",
        sa.Column("cancel_reason", sa.String(length=512), nullable=True),
    )


def downgrade() -> None:
    # WARNING: downgrade теряет аудит-трейл по всем уже отменённым task'ам:
    # `cancelled_by` (кто), `cancelled_at` (когда) и `cancel_reason` (почему)
    # вычищаются молча без бэкапа. Для prod-отката сначала экспортировать
    # содержимое таблицы (`COPY tasks(id, status, cancelled_by, cancelled_at,
    # cancel_reason) TO ...`) либо `pg_dump --table=tasks` — после drop_column
    # восстановление этих полей невозможно. Сам status='cancelled' остаётся в
    # колонке `status`, но компанию ему уже не составят поля «кто/когда/зачем».
    op.drop_column("tasks", "cancel_reason")
    op.drop_column("tasks", "cancelled_at")
    op.drop_column("tasks", "cancelled_by")
