from __future__ import annotations
from typing import Optional, List, Union, Literal
from pydantic import BaseModel, Field, ConfigDict, model_validator

class VMSnapshotBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)

class VMSnapshotCreate(VMSnapshotBase):
    vm_id: Optional[int] = Field(default=None, ge=1)

class VMSnapshotUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)

class VMSnapshotRead(VMSnapshotBase):
    id: int
    vm_id: int
    model_config = ConfigDict(from_attributes=True)

VMIdentifier = Union[int, str]

class BatchSnapshotActionRequest(BaseModel):
    vms: List[VMIdentifier] = Field(..., min_length=1, description="Список ID или имён ВМ")
    name: str = Field(..., min_length=1, max_length=100, description="Имя снимка")

class PerVMActionResult(BaseModel):
    vm_id: Optional[int] = None
    vm_name: Optional[str] = None
    status: Literal["ok", "error"]
    message: Optional[str] = None
    snapshot_id: Optional[int] = None  # для create/существующего

class BatchSnapshotActionResponse(BaseModel):
    results: List[PerVMActionResult]

class SnapshotListRequest(BaseModel):
    """Ровно одно из vm_id / vm_name + опции фильтра/пагинации."""
    vm_id: Optional[int] = Field(default=None, ge=1, description="ID ВМ")
    vm_name: Optional[str] = Field(default=None, description="Имя ВМ")
    search: Optional[str] = Field(default=None, description="Фильтр по имени снапшота (ILIKE)")
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=50, ge=1, le=500)
    order_by: str = Field(default="id")
    desc: bool = Field(default=False)

    @model_validator(mode="after")
    def _exactly_one_vm_selector(self):
        if (self.vm_id is None and self.vm_name is None) or (
            self.vm_id is not None and self.vm_name is not None
        ):
            raise ValueError("Specify exactly one of: vm_id or vm_name")
        return self