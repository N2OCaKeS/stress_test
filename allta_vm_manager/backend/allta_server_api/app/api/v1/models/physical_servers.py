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
    grade              = Column(String(120), nullable=True)
    cpu_model          = Column(String(255), nullable=True)
    cpu_total          = Column(Integer, nullable=False)
    cpu_cores_count    = Column(Integer, nullable=True)
    cpu_threads        = Column(Integer, nullable=True)
    ram_total          = Column(Integer, nullable=False)
    storage            = Column(String(255), nullable=True)
    gpu                = Column(String(255), nullable=True)
    phy_if             = Column(String(50), nullable = False, default="eth0")
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

    @property
    def os_version_name(self) -> str | None:
        if self.os_version is None:
            return None
        return str(self.os_version.name or "").strip() or None
