"""audit_events.service canonical-form check constraint

Revision ID: l2m3n4o5p6q7
Revises: k1l2m3n4o5p6
Create Date: 2026-06-05 21:00:00.000000

Retention DELETE в `repositories/retention_policies.py:247` исключает
`loging_service` через literal-сравнение `AuditEvent.service != 'loging_service'`,
рассчитывая на ingest-pydantic-валидатор (`schemas/events.py:_normalize_service`):
NFKC + invisibles + confusables + `[a-z_]{1,64}`-charset + lowercase. Любая
row, попавшая в `audit_events` мимо валидатора (миграция, ручной INSERT
support'ом, seed, прямой ORM-add из будущего сервиса) с `service =
'Loging_Service'` / `'loging_service '` / `'loging_service<U+200B>'` физически
удалялась retention sweep'ом, потому что literal `'loging_service'` с такой
строкой не совпадал.

Закрываем дыру на уровне БД: CHECK-constraint `service ~ '^[a-z_]{1,64}$'`
запрещает запись non-canonical имени, даже минуя pydantic. Регексп
совпадает один-в-один с `_SERVICE_PATTERN` в `schemas/events.py:61`.
"""
from typing import Sequence, Union

from alembic import op


revision: str = "l2m3n4o5p6q7"
down_revision: Union[str, None] = "k1l2m3n4o5p6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_CONSTRAINT_NAME = "ck_audit_events_service_canonical"
_PATTERN = r"^[a-z_]{1,64}$"


def upgrade() -> None:
    # Pre-check: если БД уже содержит non-canonical row'ы, CHECK NOT VALID
    # пропустил бы их, а добавление через `ADD CONSTRAINT ... CHECK (...)`
    # без `NOT VALID` упало бы без понятного сообщения. Ловим явным
    # SELECT'ом и фейлим миграцию с диагностикой — оператор обязан
    # руками решить, что делать с такими row'ами (нормализовать, удалить,
    # или прогнать UPDATE-санитайзер).
    bind = op.get_bind()
    from sqlalchemy import text as _text

    bad_rows = bind.execute(_text(
        """
        SELECT service, COUNT(*) AS cnt
        FROM audit_events
        WHERE service !~ :pattern
        GROUP BY service
        LIMIT 20
        """
    ), {"pattern": _PATTERN}).fetchall()
    if bad_rows:
        details = ", ".join(
            f"({row.service!r}: {row.cnt})" for row in bad_rows
        )
        raise RuntimeError(
            f"cannot add {_CONSTRAINT_NAME}: audit_events contains "
            f"non-canonical service values: {details}. Normalise or delete "
            "them before re-running this migration."
        )

    op.execute(
        f"ALTER TABLE audit_events "
        f"ADD CONSTRAINT {_CONSTRAINT_NAME} "
        f"CHECK (service ~ '{_PATTERN}')"
    )


def downgrade() -> None:
    op.execute(f"ALTER TABLE audit_events DROP CONSTRAINT IF EXISTS {_CONSTRAINT_NAME}")
