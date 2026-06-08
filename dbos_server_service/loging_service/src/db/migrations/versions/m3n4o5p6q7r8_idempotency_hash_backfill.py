"""legacy NULL idempotency_payload_hash cleanup

Revision ID: m3n4o5p6q7r8
Revises: l2m3n4o5p6q7
Create Date: 2026-06-08 12:00:00.000000

Backfill / cleanup для row'ей с `idempotency_key IS NOT NULL` и
`idempotency_payload_hash IS NULL`. Такие row'и появились на стенде
ДО миграции `j0e1f2a3b4c5` (которая добавила колонку): writer ставит
hash только на INSERT начиная с этой ревизии, на legacy ingest hash
остался NULL.

Проблема: на повторный POST с тем же `(service, idempotency_key)`
репозиторий идёт в `ON CONFLICT … DO NOTHING` ветку и сравнивает
`stored_hash` с `new_hash` (см. `repositories/events.py::insert`).
Если `stored_hash IS NULL`, сравнение тихо возвращало бы существующий
row, эффективно открывая poisoning-window: атакующий, заранее
застолбивший `(service, idempotency_key)` с NULL-hash до этой
миграции, на повторный legitimate-POST возвращал бы свой row, теряя
real-event.

Решение: для безопасной retention'ной границы (30 дней) физически
DELETE'им такие row'и. Это безопасно потому что:

  * idempotency-window legitimate outbox-retry'я измеряется
    минутами/часами — никакой реальный сервис не делает retry через
    30 дней;
  * row'и без hash и так не защищены от poisoning'а на retry;
  * legitimate `idempotency_key`-tagged row'и старше 30 дней уже
    обработаны retention'ом или не нужны для replay'я.

Для row'ей моложе 30 дней оставляем как есть — миграция не пытается
реконструировать hash из колонок (это потребовало бы повторить точную
canonical-JSON логику `_payload_hash`, и любой drift в схеме `details`
или sanitization-логике развалит сверку); вместо этого `repositories/
events.py::insert` на NULL-hash ветке логирует WARNING и трактует
такой row как legacy poison-target — caller получает 409
IDEMPOTENCY_KEY_CONFLICT, чтобы новые retry'и не маскировали legacy
NULL-hash row под legitimate replay.

Тесты:
  * `test_loging_fw11_w3.py::TestLegacyNullHashCleanup` — миграция
    удаляет row'и > 30 дней и оставляет младшие нетронутыми.
"""
from typing import Sequence, Union

from alembic import op


revision: str = "m3n4o5p6q7r8"
down_revision: Union[str, None] = "l2m3n4o5p6q7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_LEGACY_NULL_HASH_AGE_DAYS = 30


def upgrade() -> None:
    # Физический DELETE — не soft-delete: legacy row без hash и так не
    # участвует в idempotent-replay контракте; retention sweep его всё
    # равно удалил бы по retention-политике, мы лишь форсируем cleanup
    # poisoning-vector раньше.
    #
    # Граница 30 дней синхронна с дефолтом retention-policy ингест-
    # ветки и достаточно широка, чтобы покрыть любой реалистичный
    # outbox-retry window (часы, не недели).
    op.execute(
        f"""
        DELETE FROM audit_events
        WHERE idempotency_key IS NOT NULL
          AND idempotency_payload_hash IS NULL
          AND received_at < NOW() - INTERVAL '{_LEGACY_NULL_HASH_AGE_DAYS} days'
        """
    )


def downgrade() -> None:
    # No-op: восстановить удалённые row'и нельзя без backup'а. Если
    # downgrade нужен, операторам предписан pg_restore из dump'а.
    pass
