
from enum import Enum
from pydantic import BaseModel, Field


class VMTaskStatus(str, Enum):
    building = "building"
    running = "running"
    failed = "failed"


class VMTaskBase(BaseModel):
    status: VMTaskStatus = Field(..., example="building")


class VMTaskCreate(VMTaskBase):
    uuid: str = Field(..., description="UUID задачи")
    user_id: int = Field(..., description="ID пользователя, запустившего задачу")


class VMTaskUpdate(BaseModel):
    status: VMTaskStatus


class VMTaskRead(VMTaskBase):
    uuid: str
    user_id: int

    class Config:
        from_attributes = True