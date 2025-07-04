from typing import Optional
from pydantic import BaseModel, Field


class VMSnapshotBase(BaseModel):
    name: str = Field(..., example="snapshot-2024-07-05")
    description: Optional[str] = Field(None, example="Первоначальный снимок после установки")


class VMSnapshotCreate(VMSnapshotBase):
    vm_id: int = Field(..., description="ID виртуальной машины")


class VMSnapshotUpdate(BaseModel):
    name: Optional[str]
    description: Optional[str]


class VMSnapshotRead(VMSnapshotBase):
    id: int
    vm_id: int

    class Config:
        from_attributes = True