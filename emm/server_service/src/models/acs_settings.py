"""Настройки доступа к ACS — платформенный singleton.

ACS (внешний сервис — обёртка над Clonezilla/DRBL) снимает и восстанавливает
полные образы дисков физических тестовых серверов. server_service не хранит
сами снимки (их список тянется живьём из ACS), но хранит, как до ACS
достучаться: базовый URL и пароль clonezilla-сервера.

Одна строка на всю платформу (PK зафиксирован `SINGLETON_ID`), по образцу
`ProbeSettings`. `enabled=False` по умолчанию — снимки через ACS выключены,
пока account_admin явно не включит и не укажет URL и пароль.

Пароль хранится зашифрованным тем же AES-256-GCM конвертом, что и остальные
пароли server_service (`services/secrets_service.py`), под собственным AAD
(`aad_for_acs_password`), привязанным к singleton-строке.
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base

# Единственная допустимая строка таблицы. Все чтения/записи идут по этому PK.
SINGLETON_ID = "default"


class AcsSettings(Base):
    """Платформенный singleton-конфиг доступа к ACS."""

    __tablename__ = "acs_settings"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=SINGLETON_ID)
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    acs_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    acs_password_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    credential_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    migration_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    migration_owner_dept_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
