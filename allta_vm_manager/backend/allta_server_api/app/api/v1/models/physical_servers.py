import enum
from sqlalchemy import (
    Column, Integer, String, Boolean,
    ForeignKey
)
from sqlalchemy.dialects.postgresql import INET
from sqlalchemy.orm import relationship

from app.db.base import Base


class FixedServerStatus(str, enum.Enum):
    free       = "free"
    run_test   = "run test"
    debug_test = "debug test"
    vms_hub    = "vms hub"
    ready      = "ready"


class PhysicalServer(Base):
    __tablename__ = "physical_servers"

    id                = Column(Integer, primary_key=True, index=True)
    name              = Column(String(100), unique=True, nullable=False)
    ip_address        = Column(INET, unique=True, nullable=False)
    cpu_total         = Column(Integer, nullable=False)
    ram_total         = Column(Integer, nullable=False)
    virtualization    = Column(Boolean, nullable=False, default=False)
    ssh_port          = Column(Integer, nullable=False, default=22)

    server_user       = Column(String(100), nullable=False, comment="Имя пользователя на сервере")
    server_password   = Column(String,    nullable=False, comment="Пароль пользователя на сервере")

    admin_panel_user  = Column(String(100), nullable=False, comment="Логин в панель управления")
    admin_panel_pass  = Column(String,     nullable=False, comment="Пароль в панель управления")

    status            = Column(String(100), nullable=False, default=FixedServerStatus.free.value)

    os_version_id     = Column(
        Integer,
        ForeignKey("os_versions.id", ondelete="SET NULL"),
        nullable=True
    )
    os_version        = relationship("OSVersion", back_populates="servers")