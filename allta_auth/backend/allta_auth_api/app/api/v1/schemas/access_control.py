from pydantic import BaseModel, Field


class PermissionRead(BaseModel):
    id: int
    code: str
    description: str | None = None

    class Config:
        from_attributes = True


class PermissionCreate(BaseModel):
    code: str = Field(..., example="jira")
    description: str | None = Field(default=None, example="Access to Jira integration")


class RoleRead(BaseModel):
    id: int
    name: str
    description: str | None = None
    permissions: list[str] = Field(default_factory=list)


class RoleCreate(BaseModel):
    name: str = Field(..., example="auditor")
    description: str | None = Field(default=None, example="Read-only auditing role")


class RoleUpdate(BaseModel):
    name: str | None = Field(default=None, example="auditor")
    description: str | None = Field(default=None, example="Updated description")


class GroupRead(BaseModel):
    id: int
    name: str
    description: str | None = None
    permissions: list[str] = Field(default_factory=list)
    users_count: int = 0


class GroupCreate(BaseModel):
    name: str = Field(..., example="docker")
    description: str | None = Field(default=None, example="Users allowed to push docker images")


class GroupUpdate(BaseModel):
    name: str | None = Field(default=None, example="docker")
    description: str | None = Field(default=None, example="Updated group description")
