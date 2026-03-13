from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

class DriverType(str, Enum):
    ilo   = "ilo"
    idrac = "idrac"

class PhysicalServer(BaseModel):
    id: int
    name            : str
    ip_address      : str
    grade           : Optional[str] = None
    cpu_model       : Optional[str] = None
    cpu_total       : int
    cpu_cores_count : Optional[int] = None
    cpu_threads     : Optional[int] = None
    ram_total       : int
    storage         : Optional[str] = None
    gpu             : Optional[str] = None
    phy_if          : str
    virtualization  : bool = Field(False)
    ssh_port        : int = Field(22)
    driver_type     : DriverType
    admin_panel_ip  : str
    admin_panel_user: str
    admin_panel_pass: str
    status          : str
    os_version      : Optional[str] = None
    os_version_id   : Optional[int] = None

class PhysicalServerStatusOnly(BaseModel):
    id: int
    status: str


class SnapshotPasswordRead(BaseModel):
    id: int
    os_version_name: str
    ssh_username: str
    password: str
