"""Настройки SSH-доступа к хосту для управления ALLTA-сервисами — платформенный singleton.

emm крутится на том же физическом хосте, что и ACS/DRBL/Clonezilla-сервер
(owner-confirmed). Этот singleton хранит, как достучаться до хоста по SSH,
чтобы читать статус и стартовать/стопать/рестартовать systemd-юниты ALLTA
(см. `services/host_control.py`, `ALLTA_HOST_UNITS`). На хосте под это заведён
отдельный непривилегированный аккаунт с forced-command SSH
(`scripts/host-control/emm-host-service-guard.sh`) — сам guard-скрипт и
allowlist юнитов это единственная линия защиты от произвольных команд,
app-side allowlist в `host_control.py` — вторая, независимая.

Одна строка на всю платформу (PK зафиксирован `SINGLETON_ID`), по образцу
`AcsSettings`. Приватный ключ хранится зашифрованным тем же AES-256-GCM
конвертом, что и остальные секреты server_service (`services/secrets_service.py`),
под собственным AAD (`aad_for_host_control_ssh_key`), привязанным к
singleton-строке.
"""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base

# Единственная допустимая строка таблицы. Все чтения/записи идут по этому PK.
SINGLETON_ID = "default"


class HostServicesSettings(Base):
    """Платформенный singleton-конфиг SSH-доступа к хосту для host-service control."""

    __tablename__ = "host_services_settings"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=SINGLETON_ID)
    ssh_host: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ssh_port: Mapped[int] = mapped_column(Integer, nullable=False, default=22, server_default="22")
    ssh_user: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ssh_private_key_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
