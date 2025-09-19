from sqlalchemy import Column, Integer, String, ForeignKey, UniqueConstraint
from sqlalchemy.orm import relationship
from lib.base import Base

class VMSnapshot(Base):
    __tablename__ = "vm_snapshots"
    __table_args__ = (
        UniqueConstraint("vm_id", "name", name="uq_vm_snapshot_vm_id_name"),
    )

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)

    vm_id = Column(Integer, ForeignKey("vm.id", ondelete="CASCADE"), nullable=False)
    vm = relationship("VirtualMachine", back_populates="snapshots")
