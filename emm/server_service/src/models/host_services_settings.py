"""Настройки SSH-доступа к хосту для управления ALLTA-сервисами — per-department.

Каждый отдел, у которого есть свой ALLTA-хост, настраивает здесь, как до
него достучаться по SSH — одна строка на отдел (`department_id` — PK, не
платформенный singleton). Управление этой строкой скопировано на само это
отдел: `department_admin` своего отдела или носитель `admin` service-роли
`server_service` в этом же отделе (см.
`services/permissions.require_host_service_action`). `account_admin` сюда
доступа не имеет вовсе — какие сервисы отдел у себя крутит и крутит ли
что-то вообще, не должно быть видно ни платформенному админу, ни другим
отделам.

Guard-скрипт на хосте (`scripts/host-control/emm-host-service-guard.sh`) и
сгенерированный под него sudoers-файл — тоже per-department: свой аккаунт,
свой ключ, свой allowlist на своём боксе. Приватный ключ хранится
зашифрованным тем же AES-256-GCM конвертом, что и остальные секреты
server_service (`services/secrets_service.py`), под AAD, привязанным теперь
к `department_id` (`aad_for_host_control_ssh_key`) — это переходное
хранилище: `credential_id` ссылается на сервисную запись `secret_service`
(scope=service, service=host_ssh, owner_dept_id=department_id) и, когда
задан, вытесняет `ssh_private_key_encrypted` (см. `host_ssh_credential_migration.py`
и `services/host_services_settings.get_decrypted_private_key`).
"""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base


class HostServicesSettings(Base):
    """Per-department конфиг SSH-доступа к хосту для host-service control."""

    __tablename__ = "host_services_settings"

    department_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    ssh_host: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ssh_port: Mapped[int] = mapped_column(Integer, nullable=False, default=22, server_default="22")
    ssh_user: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ssh_private_key_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    credential_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    migration_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
