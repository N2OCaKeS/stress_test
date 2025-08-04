from sqlalchemy import Column, Integer, String, Text, Boolean, ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import INET
from app.db.base import Base

class VirtualMachine(Base):
    __tablename__ = "virtual_machines"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), unique=True, nullable=False)
    cpu = Column(Integer, nullable=False)
    ram = Column(Integer, nullable=False)
    ip_address = Column(INET, nullable=False)
    os = Column(String(100), nullable=False)
    kernel = Column(String(100), nullable=False)
    occupied_by = Column(Integer, nullable=True)

    status = Column(String(50), nullable=False, default="free")

    snapshots = relationship("VMSnapshot", back_populates="vm", cascade="all, delete-orphan")
