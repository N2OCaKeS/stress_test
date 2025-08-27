from enum import Enum
from typing import Optional
import re

from pydantic import BaseModel, Field, field_validator
from pydantic.networks import IPvAnyAddress

class VMFixedStatus(str, Enum):
    free = "free"
    run_test = "run test"
    debug_test = "debug test"


USERNAME_RE = re.compile(r"^[a-zA-Z0-9._-]{3,64}$")

def normalize_status(value: str) -> str:
    v = value.strip()
    v_low = v.lower().replace("_", " ")
    if v_low in {s.value for s in VMFixedStatus}:
        return VMFixedStatus(v_low).value
    if not USERNAME_RE.match(v):
        raise ValueError(
            "invalid status: should be one of fixed statuses "
            f"{[s.value for s in VMFixedStatus]} or a valid username (3-64, a-zA-Z0-9._-)"
        )
    return v

class VMBase(BaseModel):
    name: str = Field(..., examples=["vm-ubuntu-01"])
    cpu: int = Field(..., gt=0, examples=[4])
    ram: int = Field(..., gt=0, examples=[8192])
    ip_address: IPvAnyAddress = Field(..., examples=["10.0.0.55"])
    server_id: int = Field(..., examples=[2])

class VMCreate(VMBase):
    pass

class VMUpdate(BaseModel):
    name: Optional[str] = None
    cpu: Optional[int] = Field(None, gt=0)
    ram: Optional[int] = Field(None, gt=0)
    ip_address: Optional[IPvAnyAddress] = None

class VMStatusUpdate(BaseModel):
    status: str

    @field_validator("status")
    @classmethod
    def _normalize_status(cls, v: str) -> str:
        return normalize_status(v)

class VMRead(VMBase):
    id: int
    status: str
    model_config = {"from_attributes": True}
