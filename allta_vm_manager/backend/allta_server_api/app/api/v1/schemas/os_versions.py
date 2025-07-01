from typing import Optional
from pydantic import BaseModel, Field


class OSVersionBase(BaseModel):
    name: str = Field(..., example="1.8.1.6")
    description: Optional[str] = Field(None, example="RC 1.8.1")


class OSVersionCreate(OSVersionBase):
    pass


class OSVersionUpdate(BaseModel):
    name: Optional[str] = Field(None, example="1.8.1.7")
    description: Optional[str] = Field(None, example="RC 1.8.1.7")


class OSVersionRead(OSVersionBase):
    id: int

    model_config = {"from_attributes": True}
