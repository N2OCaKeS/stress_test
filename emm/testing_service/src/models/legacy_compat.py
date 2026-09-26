"""Публичный compat `/rest/api/*` для скриптов на стендах.

Скрипты веток `stress_test` ходят на легаси-хост ALLTA без авторизации
(`http://allta.devos.astralinux.ru/rest/api/get-jira-url`, `get-repo-path`,
...). После переноса DNS на платформу их обслуживает `api/legacy_public.py`
по тем же путям. Авторизации нет, поэтому доступ ограничен подсетями
источника — это данные, а не код:

* `compat_allowed_networks` — разрешённые подсети (CIDR). Сид —
  `10.177.103.0/24`: все стенды легаси (`emm/allta_app_full/
  allta_image_conf.py:34-49`, `stands_ip`) и ВМ на них
  (`make/vm_prepare/libvirt_vm.py`) лежат в ней. Выключенная строка
  остаётся в списке, но доступа не даёт.
* `legacy_compat_settings` — singleton: отдел по умолчанию для URL
  интеграций (`get-jira-url`/`get-confluence-url`). Отдел выбирается по IP
  стенда (IP → стенд в `test_stands` → его отдел, решение владельца
  24.09); незнакомый IP — этот отдел. Легаси отдавал одну глобальную
  строку (`allta_image_conf.py:81-82`), поэтому NULL — «не выбран»:
  такой запрос получает 404 с понятным кодом, а не URL случайного отдела.
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base

# Единственная строка `legacy_compat_settings`.
SINGLETON_ID = "default"


class CompatAllowedNetwork(Base):
    """Подсеть, из которой `/rest/api/*` доступен без авторизации."""

    __tablename__ = "compat_allowed_networks"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    # Нормализованная запись `ipaddress.ip_network` (`10.177.103.0/24`,
    # одиночный адрес — `/32`). UNIQUE — одна подсеть не заводится дважды.
    cidr: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(String(256), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    # NULL — строка из сида миграции.
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False,
    )


class LegacyCompatSettings(Base):
    """Платформенные настройки compat (singleton)."""

    __tablename__ = "legacy_compat_settings"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=SINGLETON_ID)
    # Отдел для URL интеграций, если IP источника не принадлежит ни одному
    # стенду (или стенды с этим IP — в разных отделах). Без FK: отделы живут
    # в auth_service.
    default_department_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False,
    )
