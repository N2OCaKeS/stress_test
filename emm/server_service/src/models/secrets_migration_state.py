"""Синглтон-строка состояния миграции секретов (force-режим перешифровки).

Force-ротация мастер-ключа переводит сервис в короткое окно обслуживания:
пока не осушены все legacy-ciphertext'ы, сервис отвечает 503 на всё, кроме
статуса перешифровки и health-проб. Флаг «force активен» обязан быть durable
и общим для всех реплик — поэтому он лежит в БД, а не в памяти процесса.

Таблица держит ровно одну строку (`id='singleton'`). Строка создаётся
миграцией, так что `get_force_active` всегда находит её; читатели, которые
столкнулись с отсутствием строки (старая БД без миграции), трактуют это как
`force_active=False`.
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, func, text
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base


class SecretsMigrationState(Base):
    """Состояние текущей миграции секретов — единственная строка на всю БД."""

    __tablename__ = "secrets_migration_state"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    # Включён ли режим форсированной перешифровки. Пока True — maintenance-gate
    # отбивает пользовательские запросы 503'ами. Снимается дренером, когда
    # legacy-остаток осушен до нуля.
    force_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
