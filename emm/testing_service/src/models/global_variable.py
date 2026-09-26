"""Каталог глобальных динамических переменных конструктора команд.

Переменная — это описание слота, а не хранилище значения: сама строка знает
только откуда значение возьмётся (`source`), какого оно типа и как получить
список допустимых значений (`choices_source`). Реальное значение приходит из
снэпшота запуска, из слота теста или живым вызовом в secret_service — в этой
таблице его нет ни в каком виде, поэтому `is_sensitive` тут метаданные:
флаг говорит воркеру и логам маскировать зарезолвленный аргумент.

`choices_source` намеренно строка, а не отдельная таблица со списком:
новое значение внутри существующего источника (очередной РЦ) не должно
требовать ни релиза, ни правки каталога. Форматы — см. `services/choices.py`.
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base


class GlobalVariable(Base):
    """Одна переменная каталога. `code` — то, чем на неё ссылается слот теста."""

    __tablename__ = "global_variables"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    label: Mapped[str] = mapped_column(String(128), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    value_type: Mapped[str] = mapped_column(String(32), nullable=False, default="string")
    choices_source: Mapped[str | None] = mapped_column(String(512), nullable=True)
    is_sensitive: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # На что ссылается переменная в своём источнике (CONTRACTS.md C1):
    # `{"template": "..."}`, `{"field": "short_name"}` и т.п. Форма зависит от
    # `source` и проверяется `services/variable_resolver.validate_source_ref`
    # при сохранении. `NULL` у источников, которым ссылка не нужна
    # (`launch_context`, `per_test_override`, `secret_service`).
    source_ref: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    # Soft-FK на auth_service identity (`usr_<hex>`/`bot_<hex>`).
    # У сидированных миграцией переменных пусто.
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
