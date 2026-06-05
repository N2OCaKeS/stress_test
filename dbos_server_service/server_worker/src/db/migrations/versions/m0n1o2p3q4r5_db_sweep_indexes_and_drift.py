"""db sweep: retention partial indexes, last_error drift, drop dead index

Revision ID: m0n1o2p3q4r5
Revises: 0005_tasks_cancel_fields
Create Date: 2026-06-05 00:00:00.000000

Бандл точечных DB-фиксов по результатам persistence-layer'а sweep'а:

1. `ix_tasks_completed_at_terminal` — partial index по `completed_at`
   с условием `status IN (succeeded/failed/cancelled)`. Покрывает
   ежедневный retention-DELETE из `repositories/task.py::delete_completed_older_than`
   (раньше был seq-scan по всей таблице).

2. `ix_tasks_started_at_running` — partial index по `started_at` для
   `status='running'`. Используется sweep'ом `list_orphaned_running`,
   который ходит каждую минуту; на горячей нагрузке избавляет от
   линейного скана.

3. `audit_outbox.last_error` приведён к `Text`. До этого был `String(512)`,
   тогда как `tasks.last_error` уже `Text` — drift. `LAST_ERROR_MAX_LEN`
   в `core/constants.py` равен 512, что прямо у границы: app-side truncate
   срабатывает корректно, но при будущем поднятии константы (и пока её
   ровно 512 — у нас включительно vs строго меньше у некоторых
   tooling-обрезок) INSERT в `audit_outbox` мог дать `StringDataRightTruncation`.
   Унификация на `Text` снимает класс ошибки и убирает рассинхрон с tasks.

4. Дропнут `ix_audit_outbox_task_id`. Поиск по коду показал — SELECT-фильтра
   по `audit_outbox.task_id` нет (только INSERT и логирование). На горячей
   INSERT-таблице каждый лишний b-tree — write-overhead и накладные на
   autovacuum. Если когда-нибудь появится debug-endpoint «выдай row'ы
   по task_id» — пересоздадим адресно.

Что НЕ делаем в этой ревизии и почему:

* `ix_tasks_status_enqueued (status, enqueued_at)` — в коде нет горячего
  SELECT'а с этим композитом (sweep идёт по partial-индексам, retention —
  через новый partial по `completed_at`). Можно было бы поменять leading
  колонку на `enqueued_at`, но «правильного» reader'а у композита всё
  равно нет — операция стала бы перебиранием порядка без бенефита.
  Оставлено как есть, отдельной чисткой можно дропнуть после
  `pg_stat_user_indexes.idx_scan` snapshot'а с прода.

* `audit_outbox` `ORDER BY created_at, id ASC` tiebreaker — это не DDL,
  правка живёт в `services/audit_outbox_publisher.py` и в docstring'е
  модели; см. `models/audit_outbox.py`.

* `worker_heartbeats` cleanup periodic — application-level, не миграция.
  Отдельный заход в `main.py` / scheduler'ом.

Downgrade-safety:

* `last_error` сужение обратно до `String(512)` потенциально теряет
  данные, если в момент downgrade в строках лежат сообщения длиннее
  512 байт. На сегодня практика этого не делает (app-side truncate), но
  formally — это data-loss, downgrade оставлен для симметрии (dev-only).
* Восстановить дропнутый `ix_audit_outbox_task_id` downgrade умеет.

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "m0n1o2p3q4r5"
down_revision: Union[str, None] = "0005_tasks_cancel_fields"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Partial index под retention DELETE по terminal-task'ам.
    op.create_index(
        "ix_tasks_completed_at_terminal",
        "tasks",
        ["completed_at"],
        postgresql_where=sa.text(
            "status IN ('succeeded','failed','cancelled')"
        ),
    )
    # Partial index под orphan-sweep по running-task'ам.
    op.create_index(
        "ix_tasks_started_at_running",
        "tasks",
        ["started_at"],
        postgresql_where=sa.text("status = 'running'"),
    )
    # Уравниваем audit_outbox.last_error с tasks.last_error (оба Text).
    op.alter_column(
        "audit_outbox",
        "last_error",
        existing_type=sa.String(length=512),
        type_=sa.Text(),
        existing_nullable=True,
    )
    # Дропаем unused index — нет ни одного WHERE-фильтра по
    # audit_outbox.task_id в коде. if_exists=True — safety на случай,
    # если index уже не существует (ручные миграции на dev-БД).
    op.drop_index(
        "ix_audit_outbox_task_id",
        table_name="audit_outbox",
        if_exists=True,
    )


def downgrade() -> None:
    # Симметричное восстановление dropped-индекса.
    op.create_index(
        "ix_audit_outbox_task_id",
        "audit_outbox",
        ["task_id"],
    )
    # Возврат типа на String(512). WARNING: если в таблице есть строки
    # длиннее 512 байт (теоретически после ослабления app-side truncate
    # cap'а), PG обрежет/упадёт — для prod-downgrade нужен предварительный
    # COPY-export. На dev-БД безопасно.
    op.alter_column(
        "audit_outbox",
        "last_error",
        existing_type=sa.Text(),
        type_=sa.String(length=512),
        existing_nullable=True,
    )
    op.drop_index(
        "ix_tasks_started_at_running",
        table_name="tasks",
    )
    op.drop_index(
        "ix_tasks_completed_at_terminal",
        table_name="tasks",
    )
