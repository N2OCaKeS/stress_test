from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, IPvAnyAddress

class IPRangeBase(BaseModel):
    name: str = Field(..., examples=["VM Subnet"])
    ip_start: IPvAnyAddress = Field(..., examples=["10.0.0.10"])
    ip_end: IPvAnyAddress = Field(..., examples=["10.0.0.250"])

class IPRangeCreate(IPRangeBase):
    pass

class IPRangeUpdate(BaseModel):
    """Частичное обновление: можно прислать любое подмножество полей."""
    name: Optional[str] = None
    ip_start: Optional[IPvAnyAddress] = None
    ip_end: Optional[IPvAnyAddress] = None

class IPRangeRead(IPRangeBase):
    id: int
    model_config = ConfigDict(from_attributes=True)
