from pydantic import BaseModel, Field
from datetime import datetime

class APITokenRead(BaseModel):
    id: int
    token: str = Field(..., description="JWT без срока жизни — сохраните его!")
    created_at: datetime
    revoked: bool

    class Config:
        from_attributes = True


class APITokenCreate(BaseModel):
    """
    Пустая — токен генерируется на сервере.
    """
    pass
