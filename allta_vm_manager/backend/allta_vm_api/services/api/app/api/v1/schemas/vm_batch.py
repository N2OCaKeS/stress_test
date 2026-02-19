from typing import Dict, List, Optional


from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic.networks import IPvAnyAddress

class BatchVMItem(BaseModel):
    ip: IPvAnyAddress = Field(..., examples=["10.0.0.1"])
    cpu: int = Field(..., gt=0, examples=[16])
    ram: int = Field(..., gt=0, examples=[131072])

    @field_validator("cpu", "ram", mode="before")
    @classmethod
    def _coerce_int(cls, v):
        if isinstance(v, str) and v.isdigit():
            return int(v)
        return v

class BatchVMCreateRequest(BaseModel):
    server_id: int = Field(..., examples=[2])
    ip_range_id: int = Field(..., examples=[1])
    vms: Dict[str, BatchVMItem] 

class BatchVMCreateError(BaseModel):
    name: str
    reason: str

class BatchVMCreateResponse(BaseModel):
    created: List[int] = []
    skipped: List[BatchVMCreateError] = []

class VMDeleteRequest(BaseModel):
    ids: Optional[List[int]] = None
    names: Optional[List[str]] = None

class ItemOpError(BaseModel):
    key: str
    reason: str

class VMDeleteResponse(BaseModel):
    deleted_ids: List[int] = []
    deleted_names: List[str] = []
    skipped: List[ItemOpError] = []

class BatchVMUpdateItem(BaseModel):
    cpu: int = Field(..., gt=0)
    ram: int = Field(..., gt=0)

    @field_validator("cpu", "ram", mode="before")
    @classmethod
    def _coerce_int(cls, v):
        if isinstance(v, str) and v.isdigit():
            return int(v)
        return v

class BatchVMUpdateRequest(BaseModel):
    vms: Dict[str, BatchVMUpdateItem]

class BatchVMUpdateResponse(BaseModel):
    updated: List[int] = []
    skipped: List[ItemOpError] = []


class AstraUpdateRequest(BaseModel):
    ids: Optional[List[int]] = Field(default=None, description="Либо список id ВМ")
    names: Optional[List[str]] = Field(default=None, description="Либо список имён ВМ")
    rc: str = Field(..., min_length=1, description="Целевая версия RC/ОС")

    @model_validator(mode="after")
    def _exactly_one_selector(self):
        has_ids = bool(self.ids)
        has_names = bool(self.names)
        if has_ids == has_names:
            raise ValueError("Provide exactly one of: ids or names")
        return self