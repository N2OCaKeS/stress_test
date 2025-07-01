from pydantic import BaseModel, IPvAnyAddress, Field

class ServiceIPRangeBase(BaseModel):
    service_name: str = Field(..., max_length=50)
    ip_from: IPvAnyAddress
    ip_to: IPvAnyAddress

class ServiceIPRangeCreate(ServiceIPRangeBase):
    ...

class ServiceIPRangeUpdate(BaseModel):
    service_name: str | None = None
    ip_from: IPvAnyAddress | None = None
    ip_to: IPvAnyAddress | None = None

class ServiceIPRangeRead(ServiceIPRangeBase):
    id: int

    model_config = {
        "from_attributes": True
    }