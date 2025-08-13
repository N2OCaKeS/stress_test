from typing import Dict, List, Optional
from ipaddress import IPv4Address, IPv6Address

from pydantic import BaseModel, Field, field_validator
from pydantic.networks import IPvAnyAddress

class BatchVMItem(BaseModel):
    ip: IPvAnyAddress = Field(..., examples=["10.177.103.101"])
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

    # допускаем "16" -> 16
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