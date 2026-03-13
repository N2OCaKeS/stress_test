from enum import Enum
from typing import Optional, Union
import re

import httpx
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, ValidationError

from app.utils.config import settings


_SERVER_NAME_RE = re.compile(r"^stand(?P<stand>\d+)_(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)$")
_LEGACY_SERVER_NAME_RE = re.compile(
    r"^(?:(?:stand)?(?P<stand>\d+))[-_](?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)$",
    flags=re.IGNORECASE,
)
_SUFFIX_SANITIZE_RE = re.compile(r"[^A-Za-z0-9._-]+")


def is_valid_server_name(name: str) -> bool:
    return bool(_SERVER_NAME_RE.fullmatch(name.strip()))


def ensure_valid_server_name(name: str) -> str:
    value = name.strip()
    if not _SERVER_NAME_RE.fullmatch(value):
        raise ValueError("name must match format 'stand<number>_<server_name>'")
    return value


def extract_stand_number(raw_name: str) -> int | None:
    value = raw_name.strip()
    if not value:
        return None

    match = _SERVER_NAME_RE.fullmatch(value)
    if match:
        return int(match.group("stand"))

    match = _LEGACY_SERVER_NAME_RE.fullmatch(value)
    if match:
        return int(match.group("stand"))

    return None


def _extract_suffix(raw_name: str) -> str | None:
    value = raw_name.strip()
    if not value:
        return None

    strict_match = _SERVER_NAME_RE.fullmatch(value)
    if strict_match:
        return strict_match.group("name").strip()

    legacy_match = _LEGACY_SERVER_NAME_RE.fullmatch(value)
    if legacy_match:
        suffix = legacy_match.group("name").strip()
        return suffix or None

    suffix = _SUFFIX_SANITIZE_RE.sub("_", value).strip("._-")
    if not suffix:
        return None
    if not re.match(r"^[A-Za-z0-9]", suffix):
        return None
    return suffix


def suggest_server_name(raw_name: str, stand_number_override: int | None = None) -> str | None:
    value = raw_name.strip()
    if not value:
        return None

    if stand_number_override is not None and stand_number_override <= 0:
        return None

    if _SERVER_NAME_RE.fullmatch(value) and stand_number_override is None:
        return value

    stand_number = stand_number_override
    if stand_number is None:
        stand_number = extract_stand_number(value)

    suffix = _extract_suffix(value)
    if stand_number is None or not suffix:
        return None

    suffix = suffix.strip()
    if not suffix:
        return None

    return f"stand{int(stand_number)}_{suffix}"


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
    model_config = ConfigDict(extra="forbid")

    name              : str                = Field(..., example="stand12_srv-main")
    ip_address        : str                = Field(..., example="10.0.0.5")
    grade             : Optional[str]      = Field(None, example="LowServer")
    cpu_model         : Optional[str]      = Field(None, example="Intel(R) Xeon(R) Silver 4210 CPU @ 2.20GHz")
    cpu_total         : int                = Field(..., example=16)
    cpu_cores_count   : Optional[int]      = Field(None, example=16)
    cpu_threads       : Optional[int]      = Field(None, example=32)
    ram_total         : int                = Field(..., example=32768)
    storage           : Optional[str]      = Field(None, example="nvme0n1 3.2Tb / SAS SSD 3.8Tb")
    gpu               : Optional[str]      = Field(None, example="NVIDIA A10")
    phy_if            : str                = Field(..., example="eth0")   
    virtualization    : bool               = Field(False, example=True)
    ssh_port          : int                = Field(22, example=2222)
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
    name              : str                = Field(..., example="stand12_srv-main")
    ip_address        : str                = Field(..., example="10.0.0.5")
    grade             : Optional[str]      = Field(None, example="LowServer")
    cpu_model         : Optional[str]      = Field(None, example="Intel(R) Xeon(R) Silver 4210 CPU @ 2.20GHz")
    cpu_total         : int                = Field(..., example=16)
    cpu_cores_count   : Optional[int]      = Field(None, example=16)
    cpu_threads       : Optional[int]      = Field(None, example=32)
    ram_total         : int                = Field(..., example=32768)
    storage           : Optional[str]      = Field(None, example="nvme0n1 3.2Tb / SAS SSD 3.8Tb")
    gpu               : Optional[str]      = Field(None, example="NVIDIA A10")
    phy_if            : str                = Field(..., example="eth0")       
    virtualization    : bool               = Field(False, example=True)
    ssh_port          : int                = Field(22, example=2222)
    driver_type       : DriverType         = Field(DriverType.ilo, example="ilo")    
    admin_panel_ip    : str                = Field(..., example="10.0.0.100")    
    admin_panel_user  : str                = Field(..., example="admin")
    admin_panel_pass  : str                = Field(..., example="secret!")
    os_version_id     : Optional[int]      = Field(None, example=1)    

    @field_validator("name")
    @classmethod
    def validate_name_format(cls, value: str) -> str:
        return ensure_valid_server_name(value)


class PhysicalServerUpdate(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    name              : Optional[str]
    ip_address        : Optional[str]
    grade             : Optional[str]
    cpu_model         : Optional[str]
    cpu_total         : Optional[int]
    cpu_cores_count   : Optional[int]
    cpu_threads       : Optional[int]
    ram_total         : Optional[int]
    storage           : Optional[str]
    gpu               : Optional[str]
    phy_if            : Optional[str]      = Field(default=None, validation_alias=AliasChoices("phy_if", "phu_if"))
    virtualization    : Optional[bool]
    ssh_port          : Optional[int]
    driver_type       : Optional[DriverType]    
    admin_panel_ip    : Optional[str]    
    admin_panel_user  : Optional[str]
    admin_panel_pass  : Optional[str]
    os_version_id     : Optional[int]

    @field_validator("name")
    @classmethod
    def validate_name_format(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        return ensure_valid_server_name(value)


class PhysicalServerStatusUpdate(BaseModel):
    status: str

    @field_validator("status")
    @classmethod
    def normalize_fixed(cls, v: str) -> str:
        norm = v.strip().lower().replace("_", " ")
        if norm in FixedServerStatus._value2member_map_:
            return FixedServerStatus(norm).value
        return v.strip()


class PhysicalServerRead(BaseModel):
    id: int
    name: str
    ip_address: str
    grade: Optional[str] = None
    cpu_model: Optional[str] = None
    cpu_total: int
    cpu_cores_count: Optional[int] = None
    cpu_threads: Optional[int] = None
    ram_total: int
    storage: Optional[str] = None
    gpu: Optional[str] = None
    phy_if: str
    virtualization: bool
    ssh_port: int
    driver_type: DriverType
    admin_panel_ip: str
    admin_panel_user: str
    admin_panel_pass: str
    status: str
    os_version: Optional[str] = Field(
        None,
        validation_alias=AliasChoices("os_version_name"),
        serialization_alias="os_version",
        example="1.8.1.6",
    )

    class Config:
        from_attributes = True
