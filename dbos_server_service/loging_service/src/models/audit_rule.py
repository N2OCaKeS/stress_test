"""ORM-модель `AuditRule` — динамические правила фильтрации событий аудита.

Правила применяются в порядке убывания `priority` (выше = первее).
Первое правило с эффектом `SUPPRESS` или `ALLOW` завершает цепочку.
`OVERRIDE_SEVERITY` меняет severity и продолжает вычисление.
"""

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base
from src.utils.ids import audit_rule_id


class AuditRule(Base):
    __tablename__ = "audit_rules"

    id: Mapped[str] = mapped_column(String(48), primary_key=True, default=audit_rule_id)
    name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # server_default зеркалит миграцию (c3d4e5f6a7b8): create_all в тестах
    # должен ставить ту же DEFAULT-метку, что `make seed`.
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )

    # Правила с большим `priority` выполняются первыми.
    priority: Mapped[int] = mapped_column(
        Integer, nullable=False, default=100, server_default="100"
    )

    # ── Критерии совпадения (None = любое значение) ───────────────────────────
    match_service: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Поддерживает glob: `user.*`, `http.*`, `user.login`.
    match_action: Mapped[str | None] = mapped_column(String(128), nullable=True)
    match_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    match_severity: Mapped[str | None] = mapped_column(String(16), nullable=True)
    match_allowed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    # ── Эффект ────────────────────────────────────────────────────────────────
    # `SUPPRESS`          — не сохранять событие.
    # `ALLOW`             — сохранить немедленно, прервать вычисление правил.
    # `OVERRIDE_SEVERITY` — изменить severity, продолжить.
    effect: Mapped[str] = mapped_column(String(32), nullable=False)
    effect_severity: Mapped[str | None] = mapped_column(String(16), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    # Soft-delete для cross-worker invalidation: физический DELETE не меняет
    # MAX(updated_at) на оставшихся row, и _RuleCache на других воркерах
    # видит «свежие» данные ещё TTL=30s. Soft-delete bump'ит updated_at у
    # самой строки, MAX растёт, кеш переезжает.
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
