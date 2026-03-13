from pydantic import BaseModel, Field


class IloCredentialRead(BaseModel):
    ip: str = Field(..., min_length=1)
    username: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)
