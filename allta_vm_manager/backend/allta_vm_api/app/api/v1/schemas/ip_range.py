from pydantic import BaseModel, Field, IPvAnyAddress
from typing import Optional


class IPRangeBase(BaseModel):
    name: str = Field(..., example="VM Subnet")
    ip_start: IPvAnyAddress = Field(..., example="10.0.0.10")
    ip_end: IPvAnyAddress = Field(..., example="10.0.0.250")


class IPRangeCreate(IPRangeBase):
    """
    При создании диапазона требуются все поля.
    """
    pass


class IPRangeUpdate(BaseModel):
    """
    При обновлении можно передавать только изменяемые поля.
    """
    name: Optional[str]
    ip_start: Optional[IPvAnyAddress]
    ip_end: Optional[IPvAnyAddress]


class IPRangeRead(IPRangeBase):
    """
    Возвращаемая модель.
    """
    id: int

    class Config:
        from_attributes = True
