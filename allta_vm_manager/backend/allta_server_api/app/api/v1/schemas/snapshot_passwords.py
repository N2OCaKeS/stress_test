from datetime import datetime

from pydantic import BaseModel, Field


class SnapshotPasswordCreate(BaseModel):
    os_version_name: str = Field(..., min_length=1, max_length=100)
    ssh_username: str = Field(..., min_length=1, max_length=100)
    password: str = Field(..., min_length=1, max_length=255)


class SnapshotPasswordUpdate(BaseModel):
    ssh_username: str | None = Field(None, min_length=1, max_length=100)
    password: str | None = Field(None, min_length=1, max_length=255)


class SnapshotPasswordRead(BaseModel):
    id: int
    os_version_name: str
    ssh_username: str
    password: str
    updated_by: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
