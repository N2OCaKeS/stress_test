"""ORM-модель `AuditEvent` — append-only журнал аудита.

Партиционирование (`PARTITION BY RANGE (timestamp)`) — открытый вопрос:
сейчас retention DELETE'ит чанками по 10k, при росте до миллионов row/день
накопление dead-tuples и index bloat станут болезненными. Решение
отложено на отдельный owner-decision (см. `obsidian/TODO.md` секция
«Open questions»).

Замечание про `uq_audit_events_service_idempotency_key`: имя с префиксом
`uq_`, но физически это partial UNIQUE INDEX (не UniqueConstraint) —
`UniqueConstraint` не поддерживает `WHERE`-предикат, поэтому только
индекс. Имя стабильно (используется в `repositories/events.py` upsert'е),
рефакторить дорого; стиль расходится с остальными `ix_*` индексами.
"""

from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, DateTime, Index, String, Boolean, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base
from src.utils.ids import audit_event_id


class AuditEvent(Base):
    __tablename__ = "audit_events"

    # Неизменяемый ID.
    id: Mapped[str] = mapped_column(String(48), primary_key=True, default=audit_event_id)

    # Когда событие произошло (по словам сервиса-источника).
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    # Когда событие приняло loging_service.
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    # Источник.
    # Single-col индекс снят в k1l2m3n4o5p6 — leading-column composite
    # `ix_audit_events_service_timestamp` покрывает.
    service: Mapped[str] = mapped_column(String(64), nullable=False)

    # Что произошло. Single-col индекс снят — фильтр всегда комбинируется
    # с `timestamp DESC`, planner выбирает composite.
    action: Mapped[str] = mapped_column(String(128), nullable=False)

    # Кто сделал. Single-col индексы по actor_id/username сняты — не
    # используются в hot-query'ах (потенциальный UI-фильтр, dead).
    actor_id: Mapped[str | None] = mapped_column(String(48), nullable=True)
    actor_type: Mapped[str] = mapped_column(String(32), nullable=False)
    username: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Single-col индекс снят — leading-column composite
    # `ix_audit_events_department_timestamp` покрывает.
    department_id: Mapped[str | None] = mapped_column(String(48), nullable=True)
    # Имя отдела актора — денормализация `department_id` для отображения,
    # симметрично паре `actor_id`/`username`. Фильтрация остаётся по
    # `department_id` (id стабилен при переименовании отдела), имя в WHERE
    # не участвует и в индексы не входит. Опционально: эмиттеры без
    # introspect-контекста (lifecycle, фон) оставляют None.
    department_name: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # Над чем. target_id mirrors source-of-truth ID schema, 48 chars cap.
    target_id: Mapped[str | None] = mapped_column(String(48), nullable=True)
    target_type: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Исход. Single-col индекс снят — фильтра только по status нет.
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    # True если операция авторизована (status != "denied"). Single-col
    # индекс снят — boolean ≈2 значения, b-tree ≈seq-scan.
    allowed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    # TRACE | DEBUG | INFO | WARNING | ERROR | CRITICAL. Single-col индекс
    # снят — селективность 1/6; будущий шаг — composite `(severity, timestamp DESC)`.
    severity: Mapped[str] = mapped_column(String(16), nullable=False, default="INFO")

    # Трассировка.
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Откуда пришёл actor. `actor_ip` — IP клиента (см. extract_client_ip:
    # left-most non-trusted из X-Forwarded-For за доверенным proxy, иначе
    # request.client.host). `user_agent` — заголовок User-Agent as-is.
    # Оба опциональны: старые эмиттеры их не шлют, доменные события без
    # request-контекста (lifecycle, фоновые задачи) оставляют None.
    actor_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(512), nullable=True)

    # Free-form технический контекст — без секретов и паролей.
    details: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    # Опциональный opaque dedup-ключ. Scope `(service, idempotency_key)`
    # через partial UNIQUE — форсится только когда ключ не NULL; legacy /
    # one-shot ingest не задеваются. Два POST'а с одинаковой парой
    # считаются одним логическим событием (outbox-retry дедуп).
    idempotency_key: Mapped[str | None] = mapped_column(
        String(128), nullable=True
    )

    # SHA-256 от canonical-JSON payload'а, заполняется только когда
    # `idempotency_key` задан. На retry с тем же ключом репозиторий
    # сверяет хэш: совпало — idempotent replay (возвращаем существующий
    # row), разошлось — 409 IDEMPOTENCY_KEY_CONFLICT. Без этой колонки
    # ON CONFLICT DO NOTHING молча возвращал бы любой первый row под
    # ключом, что позволяло держателю SERVICE_API_KEY застолбить
    # idempotency_key чужим payload'ом ("idempotency poisoning").
    idempotency_payload_hash: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )

    __table_args__ = (
        # Композитный индекс под самый частый паттерн запроса.
        Index("ix_audit_events_service_timestamp", "service", "timestamp"),
        Index("ix_audit_events_department_timestamp", "department_id", "timestamp"),
        # Partial UNIQUE — см. миграцию `h8c9d0e1f2a3`. ORM-зеркало держит
        # `Base.metadata.create_all` (используется в тестах) в синхронности
        # с Alembic'овым view схемы.
        Index(
            "uq_audit_events_service_idempotency_key",
            "service",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
        # CHECK совпадает с `_SERVICE_PATTERN` в `schemas/events.py`. Защищает
        # retention DELETE-clause (`service != 'loging_service'`) от row'ов,
        # попавших в БД мимо pydantic-валидатора: ручной INSERT, ORM-add из
        # будущего сервиса, seed. Миграция `l2m3n4o5p6q7`.
        CheckConstraint(
            "service ~ '^[a-z_]{1,64}$'",
            name="ck_audit_events_service_canonical",
        ),
    )
