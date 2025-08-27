from enum import Enum
from typing import Optional, Union

import httpx
from pydantic import BaseModel, Field, field_validator, ValidationError

from app.utils.config import settings


class FixedServerStatus(str, Enum):
    free       = "free"
    run_test   = "run test"
    debug_test = "debug test"
    vms_hub    = "vms hub"
    ready      = "ready"


class DriverType(str, Enum):
    ilo   = "ilo"
    idrac = "idrac"


class PhysicalServerBase(BaseModel):
    name              : str                = Field(..., example="server-01")
    ip_address        : str                = Field(..., example="10.0.0.5")
    cpu_total         : int                = Field(..., example=16)
    ram_total         : int                = Field(..., example=32768)
    phy_if            : str                = Field(..., example="eth0")   
    virtualization    : bool               = Field(False, example=True)
    ssh_port          : int                = Field(22, example=2222)
    server_user       : str                = Field(..., example="root")
    server_password   : str                = Field(..., example="password123")
    driver_type       : DriverType         = Field(DriverType.ilo, example="ilo")    
    admin_panel_ip    : str                = Field(..., example="10.0.0.100")    
    admin_panel_user  : str                = Field(..., example="admin")
    admin_panel_pass  : str                = Field(..., example="secret!")
    status            : str                = Field(None, example="free")
    os_version_id     : Optional[int]      = Field(None, example=1)


class PhysicalServerCreate(PhysicalServerBase):
    """
    Всё то же, что и в Base, статус по умолчанию не указывается.
    """
    name              : str                = Field(..., example="server-01")
    ip_address        : str                = Field(..., example="10.0.0.5")
    cpu_total         : int                = Field(..., example=16)
    ram_total         : int                = Field(..., example=32768)
    phy_if            : str                = Field(..., example="eth0")       
    virtualization    : bool               = Field(False, example=True)
    ssh_port          : int                = Field(22, example=2222)
    server_user       : str                = Field(..., example="root")
    server_password   : str                = Field(..., example="password123")
    driver_type       : DriverType         = Field(DriverType.ilo, example="ilo")    
    admin_panel_ip    : str                = Field(..., example="10.0.0.100")    
    admin_panel_user  : str                = Field(..., example="admin")
    admin_panel_pass  : str                = Field(..., example="secret!")
    os_version_id     : Optional[int]      = Field(None, example=1)    


class PhysicalServerUpdate(BaseModel):
    name              : Optional[str]
    ip_address        : Optional[str]
    cpu_total         : Optional[int]
    ram_total         : Optional[int]
    phu_if            : Optional[str]
    virtualization    : Optional[bool]
    ssh_port          : Optional[int]
    server_user       : Optional[str]
    server_password   : Optional[str]
    driver_type       : Optional[DriverType]    
    admin_panel_ip    : Optional[str]    
    admin_panel_user  : Optional[str]
    admin_panel_pass  : Optional[str]
    os_version_id     : Optional[int]


class PhysicalServerStatusUpdate(BaseModel):
    status: str

    @field_validator("status")
    @classmethod
    def normalize_fixed(cls, v: str) -> str:
        norm = v.strip().lower().replace("_", " ")
        if norm in FixedServerStatus._value2member_map_:
            return FixedServerStatus(norm).value
        return v.strip()


class PhysicalServerRead(PhysicalServerBase):
    id: int

    class Config:
        from_attributes = True