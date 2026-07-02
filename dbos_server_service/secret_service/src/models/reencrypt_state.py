"""Модель `reencrypt_state` — singleton-строка состояния перешифровки.

Одна строка (`id = 1`), общая для всех реплик secret_service. Держит режим
дренажа outbox'а (`lazy`/`force`), флаг активного force-окна и снапшот
прогресса, который self-drain loop обновляет на каждом тике.

Зачем в БД, а не в памяти процесса:

* реплик несколько, а force-gate должен включиться/выключиться на всех сразу;
* флаг force обязан пережить рестарт пода — иначе перезапуск в середине
  force-ротации снял бы maintenance-gate до того, как перешифровка дошла до
  конца, и часть запросов увидела бы «полуготовое» состояние ключей.

Снапшот `remaining`/`throughput`/`eta_seconds` пишет drain loop; maintenance-gate
и status-эндпоинт читают его, не пересчитывая гистограмму на каждый запрос.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    Integer,
    String,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base


MODE_LAZY = "lazy"
MODE_FORCE = "force"
REENCRYPT_MODE_VALUES = (MODE_LAZY, MODE_FORCE)

# PK singleton-строки. Таблица всегда содержит ровно одну строку.
STATE_ROW_ID = 1


class ReencryptState(Base):
    """Одна строка на весь сервис: режим дренажа + снапшот прогресса."""

    __tablename__ = "reencrypt_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)

    # lazy — фоновый троттлинг; force — плотный дренаж + maintenance-gate.
    mode: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default=text("'lazy'"),
    )

    # True, пока идёт force-окно: любой запрос (кроме status/health/ready)
    # получает 503 REENCRYPT_IN_PROGRESS. Снимается drain'ом при remaining == 0.
    force_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )

    # Снапшот прогресса, обновляется self-drain loop'ом.
    remaining: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    throughput: Mapped[float] = mapped_column(
        Float, nullable=False, server_default=text("0")
    )
    eta_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )

    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    __table_args__ = (
        CheckConstraint("id = 1", name="ck_reencrypt_state_singleton"),
        CheckConstraint(
            "mode IN ('lazy', 'force')", name="ck_reencrypt_state_mode"
        ),
    )
