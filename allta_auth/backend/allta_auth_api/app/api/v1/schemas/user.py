from typing import Any, Optional
from pydantic import BaseModel, Field, field_validator

RoleName = str


class UserBase(BaseModel):
    login: str = Field(..., example="alice")
    role: RoleName = Field("user", example="user")

    @field_validator("role", mode="before")
    @classmethod
    def normalize_role(cls, value: Any) -> str:
        if value is None:
            return "user"
        if isinstance(value, str):
            return value
        role_name = getattr(value, "name", None)
        if isinstance(role_name, str):
            return role_name
        return str(value)


class UserCreate(BaseModel):
    login: str = Field(..., example="alice")
    password: str = Field(..., example="StrongP@ssw0rd!")
    role: Optional[RoleName] = Field("user", example="guest")

    class Config:
        json_schema_extra = {
            "example": {
                "login": "bob",
                "password": "Secret123!",
                "role": "user"
            }
        }

class UserRead(UserBase):
    id: int

    class Config:
        from_attributes = True

class UserUpdate(BaseModel):
    password: Optional[str] = Field(None, example="NewPass456!")
    role: Optional[RoleName] = Field(None, example="guest")

    class Config:
        json_schema_extra = {
            "example": {
                "password": "AnotherPass!",
                "role": "admin",
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
