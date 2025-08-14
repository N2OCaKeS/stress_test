# app/api/v1/schemas/ip_range.py

from typing import Optional
from pydantic import BaseModel, Field, IPvAnyAddress

class IPRangeBase(BaseModel):
    name: str = Field(..., example="VM Subnet")
    ip_start: IPvAnyAddress = Field(..., example="10.0.0.10")
    ip_end: IPvAnyAddress = Field(..., example="10.0.0.250")

class IPRangeCreate(IPRangeBase):
    pass

class IPRangeUpdate(BaseModel):
    """Частичное обновление: можно прислать любое подмножество полей."""
    name: Optional[str] = None
    ip_start: Optional[IPvAnyAddress] = None
    ip_end: Optional[IPvAnyAddress] = None

class IPRangeRead(IPRangeBase):
    id: int
    class Config:
        from_attributes = True
