from enum import Enum
from pydantic import BaseModel, Field

class DriverType(str, Enum):
    ilo   = "ilo"
    idrac = "idrac"

class PhysicalServer(BaseModel):
    id: int
    name            : str
    ip_address      : str
    cpu_total       : int
    ram_total       : int
    virtualization  : bool = Field(False)
    ssh_port        : int = Field(22)
    server_user     : str
    server_password : str
    driver_type     : DriverType
    admin_panel_ip  : str
    admin_panel_user: str
    admin_panel_pass: str
    status          : str
    os_version_id   : int

class PhysicalServerStatusOnly(BaseModel):
    id: int
    status: str