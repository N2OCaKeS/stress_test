import enum
from sqlalchemy import (
    Column, Integer, String, Boolean,
    ForeignKey
)
from sqlalchemy.dialects.postgresql import INET
from sqlalchemy.orm import relationship

from app.db.base import Base

class DriverType(str, enum.Enum):
    ilo   = "ilo"
    idrac = "idrac"

class PhysicalServer(Base):
    __tablename__ = "physical_servers"

    id                 = Column(Integer, primary_key=True, index=True)
    name               = Column(String(100), unique=True, nullable=False)
    ip_address         = Column(INET, unique=True, nullable=False)
    cpu_total          = Column(Integer, nullable=False)
    ram_total          = Column(Integer, nullable=False)
    virtualization     = Column(Boolean, nullable=False, default=False)
    ssh_port           = Column(Integer, nullable=False, default=22)
    driver_type        = Column(
        String(10),
        nullable=False,
        default=DriverType.ilo.value,
        comment="ilo или idrac"
    )
    admin_panel_ip     = Column(INET, nullable=False)
    admin_panel_user   = Column(String(100), nullable=False)
    admin_panel_pass   = Column(String, nullable=False)
    status             = Column(String(100), nullable=False, default="free")
    os_version_id      = Column(
        Integer,
        ForeignKey("os_versions.id", ondelete="SET NULL"),
        nullable=True
    )
    os_version         = relationship("OSVersion", back_populates="servers")
