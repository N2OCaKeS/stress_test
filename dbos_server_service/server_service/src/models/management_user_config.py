"""Конфиг системной управляющей учётки — платформенный singleton.

Одна строка на всю платформу (PK зафиксирован `SINGLETON_ID`). Хранит имя
управляющего пользователя (`login`, дефолт `dbos`) и пер-режимные настройки
bootstrap'а в JSONB-колонке `modes`: на каждый режим создания учётки
(`astra_orel`/`astra_smolensk`/`astra_voronezh`/`other_os`) — список доп-групп
и список shell-команд, прогоняемых при заведении пользователя на боксе
(например выставление уровней целостности для Смоленска).

JSONB, а не таблица-строка-на-режим, выбран сознательно: набор режимов
фиксирован enum'ом, а состав пер-режимных полей будет расти (новые ключи в
объекте режима — без миграции схемы). Singleton-строки достаточно — настройка
глобальная, не привязана к департаменту.
"""

from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base

# Единственная допустимая строка таблицы. Все чтения/записи идут по этому PK.
SINGLETON_ID = "singleton"

# Дефолтное имя управляющей учётки. Совпадает с worker'ским
# `ssh_management_user` (по умолчанию `dbos`) — worker заводит ровно его на
# `server.prepare`.
DEFAULT_LOGIN = "dbos"


class ManagementUserConfig(Base):
    """Платформенный singleton-конфиг управляющей учётки."""

    __tablename__ = "management_user_config"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=SINGLETON_ID)
    login: Mapped[str] = mapped_column(
        String(32), nullable=False, default=DEFAULT_LOGIN, server_default=DEFAULT_LOGIN
    )
    # Пер-режимные настройки: {mode: {"groups": [...], "extra_create_commands": [...]}}.
    # Пустой объект по умолчанию — каждый режим тогда отдаёт дефолтные (пустые)
    # списки на чтении.
    modes: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
