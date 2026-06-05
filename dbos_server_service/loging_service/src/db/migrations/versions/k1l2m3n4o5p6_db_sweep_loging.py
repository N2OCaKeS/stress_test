"""loging db sweep: retention unique, audit_events index cleanup, retention description

Revision ID: k1l2m3n4o5p6
Revises: j0e1f2a3b4c5
Create Date: 2026-06-05 00:00:00.000000

Сборная ревизия по результатам persistence-layer обхода:

1. Partial UNIQUE на `retention_policies(severity, service) WHERE is_active = true`.
   Business-invariant «не дублировать активные политики на одну (severity, service)
   пару» до этого защищался только сервис-слоем (`deactivate_all_active` перед
   `create_policy`). Прямой вызов `create_policy` (тесты/скрипты/будущие
   bulk-importer'ы) обходил защиту.
   `NULLS NOT DISTINCT` (PG 15+) нужен потому, что глобальная политика пишется
   как (NULL, NULL), и без `NULLS NOT DISTINCT` две таких строки не считаются
   дубликатом и проходят UNIQUE.

2. Дроп 8 single-column индексов на `audit_events`. Обоснование per-index:

   - `ix_audit_events_service` — leading-column дубль `ix_audit_events_service_timestamp`;
     любой WHERE service=? покрывается composite'ом.
   - `ix_audit_events_department_id` — то же, дубль `ix_audit_events_department_timestamp`.
   - `ix_audit_events_action` — фильтр в repositories/events.py:333 всегда идёт
     совместно с `timestamp DESC` ORDER BY; bitmap-or с composite дороже,
     планировщик выбирает composite-scan.
   - `ix_audit_events_status` — нет ни одного WHERE status=? без других ключей
     (статус только пишется в hash payload).
   - `ix_audit_events_allowed` — boolean ≈2 значения; b-tree селективность
     сопоставима с seq-scan.
   - `ix_audit_events_severity` — 6 значений (TRACE..CRITICAL), низкая
     селективность; repositories/events.py:331 фильтрует severity=?, но
     планировщик предпочтёт composite `service_timestamp` если service задан.
     Будущая оптимизация — composite `(severity, timestamp DESC)`, оставлено
     на отдельную ревизию.
   - `ix_audit_events_actor_id` — фильтра по actor_id нет в repositories
     (потенциальный UI-фильтр, dead на сегодня).
   - `ix_audit_events_username` — фильтра по username нет в repositories.

   Сохранены: `ix_audit_events_timestamp` (хот ORDER BY-key для голого ingest-tail),
   composite'ы `service_timestamp`/`department_timestamp`, partial UNIQUE
   `uq_audit_events_service_idempotency_key`.

3. `retention_policies.description String(256) → Text` — forward-compatible
   widening, выравнивает с `audit_rules.description` (`Text`). Размер
   нормативной справки SOC'а 256 char недостаточен.

Downgrade — обратно симметричен (re-CREATE 8 индексов, DROP partial UNIQUE,
`description` обратно в String(256)). Pre-existing duplicates check на upgrade
выполняется до DDL — при наличии конфликта fail-fast c понятной ошибкой.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "k1l2m3n4o5p6"
down_revision: Union[str, None] = "j0e1f2a3b4c5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Индексы, удаляемые в upgrade() и пересоздаваемые в downgrade().
# Колонок одна, имя b-tree default.
_REDUNDANT_INDEXES = (
    ("ix_audit_events_service", "service"),
    ("ix_audit_events_department_id", "department_id"),
    ("ix_audit_events_action", "action"),
    ("ix_audit_events_status", "status"),
    ("ix_audit_events_allowed", "allowed"),
    ("ix_audit_events_severity", "severity"),
    ("ix_audit_events_actor_id", "actor_id"),
    ("ix_audit_events_username", "username"),
)


def upgrade() -> None:
    bind = op.get_bind()

    # 1. Pre-check на дубликаты активных политик. Если БД уже содержит
    # конфликтующие row'ы — partial UNIQUE упадёт без понятного сообщения,
    # дамп ошибки PG будет ловить admin вручную. Здесь — явный fail-fast.
    duplicate_rows = bind.execute(sa.text(
        """
        SELECT severity, service, COUNT(*) AS dup_count
        FROM retention_policies
        WHERE is_active = true
        GROUP BY severity, service
        HAVING COUNT(*) > 1
        """
    )).fetchall()
    if duplicate_rows:
        details = ", ".join(
            f"(severity={row.severity!r}, service={row.service!r}, count={row.dup_count})"
            for row in duplicate_rows
        )
        raise RuntimeError(
            "cannot create ix_retention_policies_active_unique: "
            f"existing duplicate active retention_policies rows: {details}. "
            "Run deactivate_all_active or manually deactivate duplicates "
            "before re-running this migration."
        )

    # 2. Partial UNIQUE — единая активная политика на пару (severity, service).
    # NULLS NOT DISTINCT нужно для глобальной (NULL, NULL) политики: без него
    # NULL != NULL и UNIQUE пропускает дубли.
    op.execute(
        "CREATE UNIQUE INDEX ix_retention_policies_active_unique "
        "ON retention_policies (severity, service) "
        "NULLS NOT DISTINCT "
        "WHERE is_active = true"
    )

    # 3. Дроп redundant single-column индексов на audit_events.
    for index_name, _column in _REDUNDANT_INDEXES:
        op.drop_index(index_name, table_name="audit_events", if_exists=True)

    # 4. retention_policies.description: String(256) → Text.
    op.alter_column(
        "retention_policies",
        "description",
        existing_type=sa.String(256),
        type_=sa.Text(),
        existing_nullable=True,
    )


def downgrade() -> None:
    # 4. description обратно в String(256). Если в БД успели появиться записи
    # длиннее 256 — ALTER упадёт; это ожидаемо (оператор должен сам обрезать).
    op.alter_column(
        "retention_policies",
        "description",
        existing_type=sa.Text(),
        type_=sa.String(256),
        existing_nullable=True,
    )

    # 3. Восстановить 8 single-column индексов в обратном порядке.
    for index_name, column in reversed(_REDUNDANT_INDEXES):
        op.create_index(index_name, "audit_events", [column])

    # 2. Снять partial UNIQUE. Pre-check на upgrade был аналитический и не
    # модифицировал данные, отката не требует.
    op.execute("DROP INDEX IF EXISTS ix_retention_policies_active_unique")
