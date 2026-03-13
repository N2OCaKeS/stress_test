from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import relationship

from app.db.base import Base


class SnapshotPassword(Base):
    __tablename__ = "snapshot_passwords"

    id = Column(Integer, primary_key=True, index=True)
    os_version_id = Column(
        Integer,
        ForeignKey("os_versions.id", ondelete="CASCADE"),
        unique=True,
        index=True,
        nullable=False,
    )
    ssh_username = Column(String(100), nullable=False)
    password = Column(String, nullable=False)
    updated_by = Column(String(120), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    os_version = relationship("OSVersion", back_populates="snapshot_password")

    @property
    def os_version_name(self) -> str:
        if self.os_version is None:
            return ""
        return str(self.os_version.name or "")
