"""server/vm number becomes department-scoped, mandatory

Revision ID: 36078ae57d75
Revises: e5f8a2c7d9b1
Create Date: 2026-09-22 00:00:00.000000

Номер стенда (`servers.number` / `vms.number`) переходит из «опциональный,
глобально уникальный» в «обязательный, уникальный в рамках department_id».
Две таблицы остаются раздельными constraint'ами (как и раньше) — просто
теперь composite `(department_id, number)` вместо голого `(number)`.

Backfill
--------

Для каждого `department_id` берём уже занятые номера (across both
`servers.number` и `vms.number` этого отдела, не NULL), берём их max
(0, если непустых номеров ещё не было). Всем NULL-строкам этого отдела —
вперемешку из `servers` и `vms` — назначаем новые номера последовательно
от `max+1`, в детерминированном порядке (`created_at`, при равенстве — `id`).
Единый общий счётчик на отдел не даёт новому номеру сервера столкнуться с
уже занятым номером ВМ того же отдела (и наоборот) — тот же дух, что и
раньше при глобальной уникальности, только в границах отдела.

Schema changes
--------------

* `servers.number` / `vms.number` → `SET NOT NULL`.
* Дроп `uq_servers_number` (constraint) и `uq_vms_number` (constraint).
* Новые composite unique indexes: `uq_servers_dept_stand_number` на
  `servers (department_id, number)`, `uq_vms_dept_stand_number` на
  `vms (department_id, number)`.

Downgrade
---------

Восстанавливает старые глобальные UNIQUE-constraint'ы и `nullable=True`.
Backfill не откатывается (номера остаются проставленными) — как и в
аналогичных data-migration'ах этого сервиса. Если после миграции разные
отделы успели занять одинаковый номер, восстановление глобального UNIQUE
упадёт — оператору придётся сначала развести дубликаты вручную.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "36078ae57d75"
down_revision: Union[str, None] = "e5f8a2c7d9b1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_BACKFILL_SQL = """
CREATE TEMP TABLE _stand_number_backfill ON COMMIT DROP AS
WITH dept_max AS (
    SELECT department_id, COALESCE(MAX(number), 0) AS max_number
    FROM (
        SELECT department_id, number FROM servers WHERE number IS NOT NULL
        UNION ALL
        SELECT department_id, number FROM vms WHERE number IS NOT NULL
    ) existing
    GROUP BY department_id
),
null_rows AS (
    SELECT id, department_id, 'servers' AS tbl, created_at
    FROM servers WHERE number IS NULL
    UNION ALL
    SELECT id, department_id, 'vms' AS tbl, created_at
    FROM vms WHERE number IS NULL
)
SELECT
    n.id,
    n.tbl,
    ROW_NUMBER() OVER (
        PARTITION BY n.department_id ORDER BY n.created_at, n.id
    ) + COALESCE(m.max_number, 0) AS new_number
FROM null_rows n
LEFT JOIN dept_max m ON m.department_id = n.department_id;
"""


def upgrade() -> None:
    # Старые constraint'ы глобальные (голый `number`), новые — per-department.
    # Дропаем их до backfill: иначе два отдела, независимо получающие
    # number=1 в рамках своего счётчика, сталкиваются со старым глобальным
    # UNIQUE ещё до того, как он заменяется на composite.
    op.drop_constraint("uq_servers_number", "servers", type_="unique")
    op.drop_constraint("uq_vms_number", "vms", type_="unique")

    op.execute(_BACKFILL_SQL)
    op.execute(
        """
        UPDATE servers s
        SET number = b.new_number
        FROM _stand_number_backfill b
        WHERE b.tbl = 'servers' AND b.id = s.id;
        """
    )
    op.execute(
        """
        UPDATE vms v
        SET number = b.new_number
        FROM _stand_number_backfill b
        WHERE b.tbl = 'vms' AND b.id = v.id;
        """
    )

    op.alter_column("servers", "number", nullable=False)
    op.alter_column("vms", "number", nullable=False)

    op.create_index(
        "uq_servers_dept_stand_number", "servers", ["department_id", "number"],
        unique=True,
    )
    op.create_index(
        "uq_vms_dept_stand_number", "vms", ["department_id", "number"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_vms_dept_stand_number", table_name="vms")
    op.drop_index("uq_servers_dept_stand_number", table_name="servers")

    op.create_unique_constraint("uq_vms_number", "vms", ["number"])
    op.create_unique_constraint("uq_servers_number", "servers", ["number"])

    op.alter_column("vms", "number", nullable=True)
    op.alter_column("servers", "number", nullable=True)
