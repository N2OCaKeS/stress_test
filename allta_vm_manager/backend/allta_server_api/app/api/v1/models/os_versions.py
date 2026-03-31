from sqlalchemy import JSON, Column, Integer, String, Text
from sqlalchemy.orm import relationship

from app.db.base import Base


class OSVersion(Base):
    __tablename__ = "os_versions"

    id          = Column(Integer, primary_key=True, index=True)
    name        = Column(String(100), unique=True, nullable=False)
    description = Column(Text, nullable=True)
    repository_urls = Column(JSON, nullable=False, default=list)

    servers = relationship(
        "PhysicalServer",
        back_populates="os_version",
        cascade="all, delete-orphan",
    )
    snapshot_password = relationship(
        "SnapshotPassword",
        back_populates="os_version",
        uselist=False,
        cascade="all, delete-orphan",
    )
