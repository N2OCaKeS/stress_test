from datetime import datetime
from pydantic import BaseModel, Field

class TokenCreate(BaseModel):
    username: str = Field(..., example="alice")
    password: str = Field(..., example="StrongP@ssw0rd!")

    class Config:
        json_schema_extra = {
            "example": {
                "username": "bob",
                "password": "Secret123!"
            }
        }

class TokenOut(BaseModel):
    access_token: str = Field(..., example="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...")
    token_type: str = Field("bearer", example="bearer")

class TokenData(BaseModel):
    jti: str
    user_id: int
    exp: datetime

    class Config:
        from_attributes = True
