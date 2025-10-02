from typing import Optional
from pydantic import BaseModel, Field

class UserBase(BaseModel):
    login: str = Field(..., example="alice")
    is_admin: bool = Field(False, example=True)

class UserCreate(UserBase):
    password: str = Field(..., example="StrongP@ssw0rd!")

    class Config:
        json_schema_extra = {
            "example": {
                "login": "bob",
                "password": "Secret123!",
                "is_admin": False
            }
        }

class UserRead(UserBase):
    id: int

    class Config:
        from_attributes = True

class UserUpdate(BaseModel):
    password: Optional[str] = Field(None, example="NewPass456!")
    is_admin: Optional[bool] = Field(None, example=True)

    class Config:
        json_schema_extra = {
            "example": {
                "password": "AnotherPass!",
                "is_admin": True
            }
        }

class PasswordReset(BaseModel):
    new_password: str = Field(..., example="MyNewP@ssw0rd!")

    class Config:
        json_schema_extra = {
            "example": {
                "new_password": "MyNewP@ssw0rd!"
            }
        }

class PasswordResetAdmin(BaseModel):
    new_password: str = Field(..., example="Reset1234!")

    class Config:
        json_schema_extra = {
            "example": {
                "new_password": "Reset1234!"
            }
        }
