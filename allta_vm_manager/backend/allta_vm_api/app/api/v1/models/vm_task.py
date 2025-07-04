from sqlalchemy import Column, String, Integer, Enum
from app.db.base import Base

import enum

class VMTaskStatus(str, enum.Enum):
    pending = "pending"
    running = "running"
    finished = "finished"
    failed = "failed"

class VMTask(Base):
    __tablename__ = "vm_tasks"

    uuid = Column(String(36), primary_key=True, index=True)  # UUID задачи
    status = Column(Enum(VMTaskStatus), nullable=False, default=VMTaskStatus.pending)
    user_id = Column(Integer, nullable=False)