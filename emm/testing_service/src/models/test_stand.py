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

стенд — либо физический сервер (`target_type=
server`, `server_id`, подготовка через ACS restore), либо ВМ server_service
(`target_type=vm`, `vm_id`, подготовка откатом снимка ВМ). Ровно одно из
`server_id`/`vm_id` — CHECK `ck_test_stands_target`. Все вызовы в
server_service идут через `services/stand_target.py::target_of(stand)`.
"""

from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base


class TestStand(Base):
    """Один тестовый стенд — привязка сервера/ВМ к testing_service."""

    __tablename__ = "test_stands"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    # `server` | `vm`. Дефолт — физический стенд, как было.
    target_type: Mapped[str] = mapped_column(
        String(8), nullable=False, default="server", server_default="server",
    )
    server_id: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True, index=True)
    # `Vm.id` server_service для `target_type=vm`; сырой id без FK.
    vm_id: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True, index=True)
    department_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    # Человеческое имя стенда из allta_app (`stand3`..`stand14`). Внутренний
    # `id` — `stand_<32hex>`, его нельзя ни сопоставить с именем прогона в
    # Zephyr (легаси кладёт туда `stand3`), ни показать оператору в СТП-
    # матрице. NULL — стенд, которого в легаси не было; тогда везде работает
    # fallback на `id`, а `-sn` для теста собрать не из чего.
    legacy_token: Mapped[str | None] = mapped_column(String(32), nullable=True, unique=True)
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

    __table_args__ = (
        CheckConstraint(
            "(target_type = 'server' AND server_id IS NOT NULL AND vm_id IS NULL) OR "
            "(target_type = 'vm' AND vm_id IS NOT NULL AND server_id IS NULL)",
            name="ck_test_stands_target",
        ),
    )
