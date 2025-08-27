from __future__ import annotations

from sqlalchemy import Column, Integer, String, ForeignKey
from sqlalchemy.orm import relationship

from app.db.base import Base


class VirtualMachine(Base):
    __tablename__ = "virtual_machines"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(128), unique=True, nullable=False, index=True)
    cpu = Column(Integer, nullable=False)
    ram = Column(Integer, nullable=False)
    ip_address = Column(String(64), unique=True, nullable=False, index=True)

    server_id = Column(Integer, ForeignKey("physical_servers.id"), nullable=False, index=True)
    status = Column(String(64), nullable=True, index=True)


    password_enc = Column(String(1024), nullable=True)

    server = relationship("PhysicalServer", back_populates="vms")
