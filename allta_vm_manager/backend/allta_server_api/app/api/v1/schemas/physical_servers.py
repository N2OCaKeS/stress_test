# backend/allta_server_api/app/api/v1/schemas/physical_servers.py
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field, field_validator, ValidationError

import httpx
from app.utils.config import settings


class FixedServerStatus(str, Enum):
    free       = "free"
    run_test   = "run test"
    debug_test = "debug test"
    vms_hub    = "vms hub"
    ready      = "ready"


class PhysicalServerBase(BaseModel):
    name              : str  = Field(..., example="server-01")
    ip_address        : str  = Field(..., example="10.0.0.5")
    cpu_total         : int  = Field(..., example=16)
    ram_total         : int  = Field(..., example=32768)
    virtualization    : bool = Field(False, example=True)
    ssh_port          : int  = Field(22, example=2222)

    # новые поля
    server_user       : str  = Field(..., example="root")
    server_password   : str  = Field(..., example="password123")

    admin_panel_user  : str  = Field(..., example="admin")
    admin_panel_pass  : str  = Field(..., example="secret!")

    os_version_id     : Optional[int] = Field(None, example=3)


class PhysicalServerCreate(PhysicalServerBase):
    pass


class PhysicalServerUpdate(BaseModel):
    name              : Optional[str]
    ip_address        : Optional[str]
    cpu_total         : Optional[int]
    ram_total         : Optional[int]
    virtualization    : Optional[bool]
    ssh_port          : Optional[int]

    server_user       : Optional[str]
    server_password   : Optional[str]

    admin_panel_user  : Optional[str]
    admin_panel_pass  : Optional[str]

    os_version_id     : Optional[int]


class PhysicalServerStatusUpdate(BaseModel):
    status: str = Field(
        ...,
        description="Новый статус: одно из фиксированных или логин пользователя"
    )

    @field_validator("status")
    @classmethod
    def validate_status_or_user(cls, v):
        if v in FixedServerStatus.__members__.values():
            return v
        try:
            resp = httpx.get(
                f"{settings.AUTH_API_URL}/verify?login={v}",
                timeout=3.0,
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                raise ValueError(f"user '{v}' not exist")
            raise ValueError("Error verify user")
        except httpx.RequestError:
            raise ValueError("Auth API is not available")
        return v


class PhysicalServerRead(PhysicalServerBase):
    id: int

    class Config:
        from_attributes = True
