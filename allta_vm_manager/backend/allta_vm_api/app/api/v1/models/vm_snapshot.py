
from sqlalchemy import Column, Integer, String, Text, ForeignKey
from sqlalchemy.orm import relationship
from app.db.base import Base

class VMSnapshot(Base):
    __tablename__ = "vm_snapshots"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    description = Column(Text, nullable=True)

    vm_id = Column(Integer, ForeignKey("virtual_machines.id", ondelete="CASCADE"), nullable=False)
    vm = relationship("VirtualMachine", back_populates="snapshots")