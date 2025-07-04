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
    virtualization    : bool               = Field(False, example=True)
    ssh_port          : int                = Field(22, example=2222)
    driver_type       : DriverType         = Field(DriverType.ilo, example="ilo")
    admin_panel_ip    : str                = Field(..., example="10.0.0.100")
    server_user       : str                = Field(..., example="root")
    server_password   : str                = Field(..., example="password123")
    admin_panel_user  : str                = Field(..., example="admin")
    admin_panel_pass  : str                = Field(..., example="secret!")
    os_version_id     : Optional[int]      = Field(None, example=3)


class PhysicalServerCreate(PhysicalServerBase):
    """
    Всё то же, что и в Base, статус по умолчанию не указывается.
    """
    pass


class PhysicalServerUpdate(BaseModel):
    name              : Optional[str]
    ip_address        : Optional[str]
    cpu_total         : Optional[int]
    ram_total         : Optional[int]
    virtualization    : Optional[bool]
    ssh_port          : Optional[int]
    driver_type       : Optional[DriverType]
    admin_panel_ip    : Optional[str]
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
        # если это фиксированный статус — ок
        if v in FixedServerStatus.__members__.values():
            return v
        # иначе проверяем существование логина через Auth-сервис
        try:
            resp = httpx.get(
                f"{settings.AUTH_API_URL}/verify?login={v}",
                timeout=3.0,
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                raise ValueError(f"Пользователь '{v}' не найден")
            raise ValueError("Ошибка при проверке пользователя в Auth-сервисе")
        except httpx.RequestError:
            raise ValueError("Не удалось подключиться к Auth-сервису")
        return v


class PhysicalServerRead(PhysicalServerBase):
    id: int

    class Config:
        from_attributes = True