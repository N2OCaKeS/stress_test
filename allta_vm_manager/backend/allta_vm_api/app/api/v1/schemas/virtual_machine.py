from typing import Optional
from pydantic import BaseModel, Field


class VMBase(BaseModel):
    name: str = Field(..., example="vm-ubuntu-01")
    cpu: int = Field(..., example=4)
    ram: int = Field(..., example=8192)   # в МБ
    disk: int = Field(..., example=100)   # в ГБ
    os: str = Field(..., example="Ubuntu 22.04")
    kernel: str = Field(None, example="5.15.0-105-generic")
    ip_address: str = Field(None, example="10.0.0.55")


class VMCreate(VMBase):
    pass


class VMUpdate(BaseModel):
    name: Optional[str]
    cpu: Optional[int]
    ram: Optional[int]
    disk: Optional[int]
    os: Optional[str]
    kernel: Optional[str]
    ip_address: Optional[str]
    occupied_by: Optional[int]
    status: Optional[str]


class VMRead(VMBase):
    id: int
    occupied_by: Optional[int]
    status: str

    class Config:
        from_attributes = True