"""Тестовый стенд — надстройка над Server/Vm из server_service (§2.3, §4 плана миграции).

`test_stands` не хранит паспортные данные сервера — только то, что специфично
для testing_service (участвует ли стенд в очереди, активен ли). IP/OS/ядро/
категория читаются живым запросом к server_service, не дублируются здесь.

`server_id` — сырой id без FK: межсервисная ссылка на `Server`/`Vm.id`,
целостность держит application code, тот же приём, что и у
`test_definitions.pinned_stand_id`.

`department_id` не принимается от клиента при создании стенда — резолвится
живым запросом к server_service (`server_client.get_server`) в момент
создания, чтобы не было спуфинга или дрейфа от реального
`Server.department_id`.

`UNIQUE(server_id)` — один физический/виртуальный сервер не может быть двумя
разными стендами одновременно.
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base


class TestStand(Base):
    """Один тестовый стенд — привязка сервера/ВМ к testing_service."""

    __tablename__ = "test_stands"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    server_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    department_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    queue_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Soft-FK на auth_service identity (`usr_<hex>`/`bot_<hex>`).
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
