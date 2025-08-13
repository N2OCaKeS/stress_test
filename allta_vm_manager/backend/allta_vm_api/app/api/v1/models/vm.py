# app/api/v1/models/vm.py
from sqlalchemy import Column, Integer, String
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import INET
from app.db.base import Base

class VirtualMachine(Base):
    __tablename__ = "vm"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), unique=True, nullable=False)
    cpu = Column(Integer, nullable=False)
    ram = Column(Integer, nullable=False)
    ip_address = Column(INET, nullable=False)
    status = Column(String(50), nullable=False, default="free")
    server_id = Column(Integer, nullable=False, index=True)  # <<< НОВОЕ ПОЛЕ

    snapshots = relationship(
        "VMSnapshot",
        back_populates="vm",
        cascade="all, delete-orphan",
    )
