from pydantic import BaseModel, Field
from typing import List, Optional


class Stand(BaseModel):
    id: Optional[int] = None
    name: str = Field(max_length=50)
    storage_name: str = Field(max_length=50)
    ip: str = Field(max_length=16)
    user_admin: str = Field(max_length=30)
    pass_admin: str = Field(max_length=50)

class Version(BaseModel):
    id: Optional[int] = None
    name: str = Field(max_length=50)
    digit_name: str = Field(max_length=50)

class Snapshot(BaseModel):
    id: Optional[int] = None
    name: str
    version: Optional[List[Version]] = []
    stand: Optional[List[Stand]] = []
    
class Repo(BaseModel):
    id: Optional[int] = None 
    link: str
    # version: Optional[List[Version]] = []
    version_id: int
    
    # class Config:
    #     orm_mode = True