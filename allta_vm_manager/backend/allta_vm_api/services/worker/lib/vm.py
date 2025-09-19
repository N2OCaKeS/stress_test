from __future__ import annotations

from sqlalchemy import Column, Integer, String
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import INET
from lib.base import Base


class VirtualMachine(Base):
    __tablename__ = "vm"

    id = Column(Integer, primary_key=True, index=True)    
    name = Column(String(128), unique=True, nullable=False, index=True)
    cpu = Column(Integer, nullable=False)
    ram = Column(Integer, nullable=False)
    ip_address = Column(INET, nullable=False)
    status = Column(String(50), nullable=False, default="free")
    server_id = Column(Integer, nullable=False, index=True)
    status = Column(String(64), nullable=True, index=True)
    password_enc = Column(String, nullable=True)

    snapshots = relationship("VMSnapshot", back_populates="vm", cascade="all, delete-orphan")
